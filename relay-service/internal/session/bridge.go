package session

import (
	"bufio"
	"context"
	"crypto/tls"
	"io"
	"net"
	"strings"
	"time"

	"relay-service/internal/logx"
)

type Bridge struct {
	addr     string
	rtspAddr string
	store    tunnelLookup
	TLS      *tls.Config
}

type tunnelLookup interface {
	ByClient(context.Context, string) (Tunnel, error)
	ByTunnel(context.Context, string) (Tunnel, error)
}

func NewBridge(addr, rtspAddr string, store *Store) *Bridge {
	return &Bridge{addr: addr, rtspAddr: rtspAddr, store: store}
}

func (b *Bridge) Serve(ctx context.Context) error {
	ln, err := net.Listen("tcp", b.addr)
	if err == nil && b.TLS != nil {
		ln = tls.NewListener(ln, b.TLS)
	}
	if err != nil {
		return err
	}
	go func() {
		<-ctx.Done()
		_ = ln.Close()
	}()
	for {
		conn, err := ln.Accept()
		if err != nil {
			if ctx.Err() != nil {
				return nil
			}
			logx.Error("session accept", map[string]any{"err": err.Error()})
			continue
		}
		go b.handle(ctx, conn)
	}
}

func (b *Bridge) handle(ctx context.Context, front net.Conn) {
	defer front.Close()
	_ = front.SetDeadline(time.Now().Add(5 * time.Second))
	reader := bufio.NewReader(front)
	line, err := reader.ReadSlice('\n')
	if err != nil {
		return
	}
	_ = front.SetDeadline(time.Time{})
	role, clientID, ok := parseHello(string(line))
	if !ok {
		return
	}
	tunnel, err := b.store.ByClient(ctx, clientID)
	if err != nil {
		logx.Info("session denied", map[string]any{"role": role})
		return
	}
	back, err := net.DialTimeout("tcp", b.rtspAddr, 3*time.Second)
	if err != nil {
		logx.Error("rtsp dial", map[string]any{"err": err.Error()})
		return
	}
	defer back.Close()
	logx.Info("session joined", map[string]any{"role": role, "tunnel_id": tunnel.TunnelID})
	done := make(chan struct{}, 2)
	go func() {
		_, _ = io.Copy(back, reader)
		done <- struct{}{}
	}()
	go func() {
		_, _ = io.Copy(front, back)
		done <- struct{}{}
	}()
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-done:
			return
		case <-ctx.Done():
			return
		case <-ticker.C:
			check, cancel := context.WithTimeout(ctx, time.Second)
			_, err := b.store.ByTunnel(check, tunnel.TunnelID)
			cancel()
			if err != nil {
				return
			}
		}
	}
}

func parseHello(line string) (role, clientID string, ok bool) {
	fields := strings.Fields(strings.TrimSpace(line))
	if len(fields) != 2 {
		return "", "", false
	}
	role = strings.ToUpper(fields[0])
	if role != "CLIENT" && role != "OPERATOR" {
		return "", "", false
	}
	return role, fields[1], true
}
