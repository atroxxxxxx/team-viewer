package session

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestExactRevocationAndRenewal(t *testing.T) {
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("requires an isolated Redis instance")
	}

	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	store := Open(addr)
	defer store.rdb.Close()
	old := Tunnel{TunnelID: uuid.NewString(), TicketID: uuid.NewString(), ClientID: uuid.NewString()}
	replacement := Tunnel{TunnelID: uuid.NewString(), TicketID: old.TicketID, ClientID: old.ClientID}
	if err := store.Put(ctx, old); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		// A separate store may be closed by normal test teardown before cleanup.
		clean := Open(addr)
		defer clean.rdb.Close()
		_ = clean.DeleteTunnel(context.Background(), old.TicketID, old.TunnelID)
		_ = clean.DeleteTunnel(context.Background(), replacement.TicketID, replacement.TunnelID)
	})
	if err := store.Put(ctx, replacement); err != nil {
		t.Fatal(err)
	}
	if err := store.DeleteTunnel(ctx, old.TicketID, old.TunnelID); err != nil {
		t.Fatal(err)
	}
	if _, err := store.ByTunnel(ctx, old.TunnelID); err != ErrDenied {
		t.Fatalf("old tunnel must be revoked, got %v", err)
	}
	if current, err := store.ByClient(ctx, old.ClientID); err != nil || current.TunnelID != replacement.TunnelID {
		t.Fatalf("new connection must survive, got %v (%v)", current.TunnelID, err)
	}
	if err := store.rdb.Expire(ctx, "tunnel:"+replacement.TunnelID, time.Minute).Err(); err != nil {
		t.Fatal(err)
	}
	if err := store.RenewTunnel(ctx, replacement.TicketID, replacement.TunnelID); err != nil {
		t.Fatal(err)
	}
	if ttl, err := store.rdb.TTL(ctx, "tunnel:"+replacement.TunnelID).Result(); err != nil || ttl < 14*time.Minute {
		t.Fatalf("valid lease must be renewed, got %v (%v)", ttl, err)
	}
	if err := store.RenewTunnel(ctx, old.TicketID, old.TunnelID); err != ErrDenied {
		t.Fatalf("revoked lease must stay revoked, got %v", err)
	}
}
