package session

import (
	"bufio"
	"context"
	"io"
	"net"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestHelloLeavesRTSPStreamIntact(t *testing.T) {
	hello := "CLIENT 11111111-1111-1111-1111-111111111111 " + strings.Repeat("a", 64) + "\n"
	payload := "OPTIONS rtsp://127.0.0.1/screen RTSP/1.0\r\n" + strings.Repeat("x", 4096)
	reader := bufio.NewReaderSize(strings.NewReader(hello+payload), maxHelloBytes)
	if _, _, _, ok := readHello(reader); !ok {
		t.Fatal("валидное приветствие отклонено")
	}
	got, err := io.ReadAll(reader)
	if err != nil || string(got) != payload {
		t.Fatalf("поток RTSP повреждён: получено %d из %d байт, ошибка %v", len(got), len(payload), err)
	}
}

func TestHelloLengthLimit(t *testing.T) {
	hello := "OPERATOR 11111111-1111-1111-1111-111111111111 " + strings.Repeat("a", 64)
	for _, size := range []int{maxHelloBytes, maxHelloBytes + 1} {
		line := hello + strings.Repeat(" ", size-len(hello)-1) + "\n"
		reader := bufio.NewReaderSize(strings.NewReader(line), maxHelloBytes)
		if _, _, _, ok := readHello(reader); ok != (size == maxHelloBytes) {
			t.Errorf("неверный результат для приветствия длиной %d: %v", size, ok)
		}
	}
}

func TestParseHelloRequiresRoleSecret(t *testing.T) {
	secret := "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
	role, clientID, got, ok := parseHello("CLIENT 11111111-1111-1111-1111-111111111111 " + secret + "\n")
	if !ok || role != "CLIENT" || clientID != "11111111-1111-1111-1111-111111111111" || got != secret {
		t.Fatalf("hello не разобран: %v %v %v %v", role, clientID, got, ok)
	}
	if _, _, _, ok := parseHello("CLIENT 11111111-1111-1111-1111-111111111111\n"); ok {
		t.Fatal("строка без секрета не должна приниматься")
	}
	if _, _, _, ok := parseHello("CLIENT not-a-uuid " + secret + "\n"); ok {
		t.Fatal("некорректный uuid не должен приниматься")
	}
}

func TestTunnelSecretIsRoleSpecific(t *testing.T) {
	client := "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	operator := "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
	item := Tunnel{ClientSecret: client, OperatorSecret: operator}
	if !item.allows("CLIENT", client) || !item.allows("OPERATOR", operator) {
		t.Fatal("свой секрет роли должен проходить")
	}
	if item.allows("OPERATOR", client) || item.allows("CLIENT", operator) || item.allows("CLIENT", "") {
		t.Fatal("чужой секрет не должен открывать роль")
	}
	if (Tunnel{}).allows("CLIENT", client) {
		t.Fatal("туннель без секрета должен быть закрыт")
	}
}

type testTunnelLookup struct{ revoked atomic.Bool }

func (*testTunnelLookup) ByClient(context.Context, string) (Tunnel, error) {
	return Tunnel{TunnelID: "test", OperatorSecret: strings.Repeat("a", 64)}, nil
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
	if _, err := io.WriteString(client, "OPERATOR 11111111-1111-1111-1111-111111111111 "+strings.Repeat("a", 64)+"\nready"); err != nil {
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
