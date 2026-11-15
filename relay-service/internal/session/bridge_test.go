package session

import (
	"context"
	"io"
	"net"
	"sync/atomic"
	"testing"
	"time"
)

type testTunnelLookup struct{ revoked atomic.Bool }

func (*testTunnelLookup) ByClient(context.Context, string) (Tunnel, error) {
	return Tunnel{TunnelID: "test"}, nil
}

func (s *testTunnelLookup) ByTunnel(context.Context, string) (Tunnel, error) {
	if s.revoked.Load() {
		return Tunnel{}, ErrDenied
	}
	return Tunnel{TunnelID: "test"}, nil
}

func TestBridgeDisconnectsRevokedTunnel(t *testing.T) {
	backend, err := net.ListenTCP("tcp", &net.TCPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil {
		t.Fatal(err)
	}
	defer backend.Close()
	deadline := time.Now().Add(4 * time.Second)
	_ = backend.SetDeadline(deadline)
	front, client := net.Pipe()
	defer client.Close()
	_ = client.SetDeadline(deadline)

	store := &testTunnelLookup{}
	b := &Bridge{rtspAddr: backend.Addr().String(), store: store}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go b.handle(ctx, front)
	if _, err := io.WriteString(client, "OPERATOR test\nready"); err != nil {
		t.Fatal(err)
	}
	peer, err := backend.AcceptTCP()
	if err != nil {
		t.Fatal(err)
	}
	defer peer.Close()
	_ = peer.SetDeadline(deadline)
	buf := make([]byte, 5)
	if _, err := io.ReadFull(peer, buf); err != nil || string(buf) != "ready" {
		t.Fatalf("expected payload from active tunnel, got %q (%v)", buf, err)
	}
	store.revoked.Store(true)
	if _, err := client.Read(buf); err != io.EOF {
		t.Fatalf("expected revoked connection to close, got %v", err)
	}
	if _, err := peer.Read(buf); err != io.EOF {
		t.Fatalf("expected relay backend to close, got %v", err)
	}
}
