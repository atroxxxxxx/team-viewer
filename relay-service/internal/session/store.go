package session

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

const TTL = 15 * time.Minute

var ErrDenied = errors.New("denied")

type Tunnel struct {
	TunnelID string `json:"tunnel_id"`
	ClientID string `json:"client_id"`
	TicketID string `json:"ticket_id"`
	Camera   bool   `json:"camera"`
}

type Store struct {
	rdb *redis.Client
}

func Open(addr string) *Store {
	return &Store{rdb: redis.NewClient(&redis.Options{Addr: addr})}
}

func (s *Store) Ping(ctx context.Context) error {
	return s.rdb.Ping(ctx).Err()
}

func (s *Store) Put(ctx context.Context, item Tunnel) error {
	raw, err := json.Marshal(item)
	if err != nil {
		return err
	}
	pipe := s.rdb.TxPipeline()
	pipe.Set(ctx, "tunnel:"+item.TunnelID, raw, TTL)
	pipe.Set(ctx, "client:"+item.ClientID, item.TunnelID, TTL)
	pipe.Set(ctx, "ticket:"+item.TicketID, item.TunnelID, TTL)
	_, err = pipe.Exec(ctx)
	return err
}

func (s *Store) ByClient(ctx context.Context, clientID string) (Tunnel, error) {
	tunnelID, err := s.rdb.Get(ctx, "client:"+clientID).Result()
	if err != nil {
		return Tunnel{}, ErrDenied
	}
	return s.ByTunnel(ctx, tunnelID)
}

func (s *Store) ByTunnel(ctx context.Context, tunnelID string) (Tunnel, error) {
	raw, err := s.rdb.Get(ctx, "tunnel:"+tunnelID).Bytes()
	if err != nil {
		return Tunnel{}, ErrDenied
	}
	var item Tunnel
	if err := json.Unmarshal(raw, &item); err != nil {
		return Tunnel{}, ErrDenied
	}
	return item, nil
}

func (s *Store) DeleteByTicket(ctx context.Context, ticketID string) error {
	tunnelID, err := s.rdb.Get(ctx, "ticket:"+ticketID).Result()
	if errors.Is(err, redis.Nil) {
		return nil
	}
	if err != nil {
		return err
	}
	item, err := s.ByTunnel(ctx, tunnelID)
	if err != nil {
		return s.rdb.Del(ctx, "ticket:"+ticketID, "tunnel:"+tunnelID).Err()
	}
	return s.rdb.Del(ctx, "ticket:"+ticketID, "tunnel:"+tunnelID, "client:"+item.ClientID).Err()
}

func (s *Store) AllowPath(ctx context.Context, path string) error {
	parts := strings.Split(strings.Trim(path, "/"), "/")
	if len(parts) != 3 || parts[0] != "tunnels" {
		return ErrDenied
	}
	if _, err := uuid.Parse(parts[1]); err != nil {
		return ErrDenied
	}
	item, err := s.ByTunnel(ctx, parts[1])
	if err != nil {
		return ErrDenied
	}
	switch parts[2] {
	case "screen":
		return nil
	case "camera":
		if !item.Camera {
			return ErrDenied
		}
		return nil
	default:
		return ErrDenied
	}
}
