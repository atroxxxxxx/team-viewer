package session

import (
	"bufio"
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"io"
	"net"
	"strings"
	"time"

	"relay-service/internal/logx"

	"github.com/google/uuid"
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
	reader := bufio.NewReader(io.LimitReader(front, 256))
	line, err := reader.ReadString('\n')
	if err != nil || len(line) > 200 {
		return
	}
	_ = front.SetReadDeadline(time.Time{})
	role, clientID, secret, ok := parseHello(line)
	if !ok {
		return
	}
	item, err := b.store.ByClient(context.Background(), clientID)
	if err != nil || !item.allows(role, secret) {
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
