package session

import (
	"bufio"
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"crypto/tls"
	"errors"
	"io"
	"net"
	"strings"
	"time"

	"relay-service/internal/logx"

	"github.com/google/uuid"
)

const maxHelloBytes = 200

type Bridge struct {
	addr     string
	rtspAddr string
	store    tunnelLookup
	peers    *mediaPeers
	TLS      *tls.Config
}

type tunnelLookup interface {
	ByClient(context.Context, string) (Tunnel, error)
	ByTunnel(context.Context, string) (Tunnel, error)
}

func NewBridge(addr, rtspAddr string, store *Store) *Bridge {
	return &Bridge{addr: addr, rtspAddr: rtspAddr, store: store, peers: &store.peers}
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
	reader := bufio.NewReaderSize(front, maxHelloBytes)
	role, identifier, secret, ok := readHello(reader)
	if !ok {
		return
	}
	_ = front.SetDeadline(time.Time{})
	lookup, cancel := context.WithTimeout(ctx, time.Second)
	item, err := b.store.ByTunnel(lookup, identifier)
	if errors.Is(err, ErrDenied) {
		// Older GUIs send the device UUID rather than the tunnel UUID.
		item, err = b.store.ByClient(lookup, identifier)
	}
	cancel()
	if err != nil || !item.allows(role, secret) {
		logx.Info("session denied", map[string]any{"role": role})
		return
	}
	back, err := net.DialTimeout("tcp", b.rtspAddr, 3*time.Second)
	if err != nil {
		logx.Error("rtsp dial", map[string]any{"err": err.Error()})
		return
	}
	defer back.Close()
	if b.peers != nil {
		unbind := b.peers.bind(back.LocalAddr(), item, role)
		defer unbind()
	}
	logx.Info("session joined", map[string]any{"role": role, "tunnel_id": item.TunnelID})
	done := make(chan struct{}, 2)
	go func() {
		// Keep using reader so bytes buffered alongside the hello reach RTSP.
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
			_, err := b.store.ByTunnel(check, item.TunnelID)
			cancel()
			if err != nil {
				return
			}
		}
	}
}

func readHello(reader *bufio.Reader) (role, clientID, secret string, ok bool) {
	line, err := reader.ReadSlice('\n')
	if err != nil || len(line) > maxHelloBytes {
		return "", "", "", false
	}
	return parseHello(string(line))
}

func parseHello(line string) (role, clientID, secret string, ok bool) {
	fields := strings.Fields(strings.TrimSpace(line))
	if len(fields) != 3 {
		return "", "", "", false
	}
	role = strings.ToUpper(fields[0])
	if role != "CLIENT" && role != "OPERATOR" {
		return "", "", "", false
	}
	if _, err := uuid.Parse(fields[1]); err != nil {
		return "", "", "", false
	}
	if len(fields[2]) != 64 {
		return "", "", "", false
	}
	return role, fields[1], fields[2], true
}

func (item Tunnel) allows(role, secret string) bool {
	var want string
	switch role {
	case "CLIENT":
		want = item.ClientSecret
	case "OPERATOR":
		want = item.OperatorSecret
	default:
		return false
	}
	if len(want) != 64 || len(secret) != 64 {
		return false
	}
	gotSum := sha256.Sum256([]byte(secret))
	wantSum := sha256.Sum256([]byte(want))
	return subtle.ConstantTimeCompare(gotSum[:], wantSum[:]) == 1
}
