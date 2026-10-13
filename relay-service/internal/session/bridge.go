package session

import (
	"bufio"
	"context"
	"io"
	"net"
	"strings"
	"time"

	"relay-service/internal/logx"
)

type Bridge struct {
	addr     string
	rtspAddr string
	store    *Store
}

func NewBridge(addr, rtspAddr string, store *Store) *Bridge {
	return &Bridge{addr: addr, rtspAddr: rtspAddr, store: store}
}

func (b *Bridge) Serve(ctx context.Context) error {
	ln, err := net.Listen("tcp", b.addr)
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
		go b.handle(conn)
	}
}

func (b *Bridge) handle(front net.Conn) {
	defer front.Close()
	_ = front.SetReadDeadline(time.Now().Add(5 * time.Second))
	reader := bufio.NewReader(front)
	line, err := reader.ReadString('\n')
	if err != nil {
		return
	}
	_ = front.SetReadDeadline(time.Time{})
	role, clientID, ok := parseHello(line)
	if !ok {
		return
	}
	if _, err := b.store.ByClient(context.Background(), clientID); err != nil {
		logx.Info("session denied", map[string]any{"role": role})
		return
	}
	back, err := net.Dial("tcp", b.rtspAddr)
	if err != nil {
		logx.Error("rtsp dial", map[string]any{"err": err.Error()})
		return
	}
	defer back.Close()
	if reader.Buffered() > 0 {
		pending := make([]byte, reader.Buffered())
		if _, err := io.ReadFull(reader, pending); err != nil {
			return
		}
		if _, err := back.Write(pending); err != nil {
			return
		}
	}
	logx.Info("session joined", map[string]any{"role": role, "client_id": clientID})
	done := make(chan struct{}, 2)
	go func() {
		_, _ = io.Copy(back, reader)
		done <- struct{}{}
	}()
	go func() {
		_, _ = io.Copy(front, back)
		done <- struct{}{}
	}()
	<-done
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
