package session

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/redis/go-redis/v9"
)

const TTL = 15 * time.Minute

var ErrDenied = errors.New("denied")

type Tunnel struct {
	TunnelID       string `json:"tunnel_id"`
	ClientID       string `json:"client_id"`
	TicketID       string `json:"ticket_id"`
	Camera         bool   `json:"camera"`
	ClientSecret   string `json:"client_secret"`
	OperatorSecret string `json:"operator_secret"`
}

type Store struct {
	rdb   *redis.Client
	peers mediaPeers
}

func Open(addr string) *Store {
	return &Store{rdb: redis.NewClient(&redis.Options{Addr: addr, ContextTimeoutEnabled: true})}
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
		if errors.Is(err, redis.Nil) {
			return Tunnel{}, ErrDenied
		}
		return Tunnel{}, err
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
	return s.DeleteTunnel(ctx, ticketID, tunnelID)
}

func (s *Store) DeleteTunnel(ctx context.Context, ticketID, tunnelID string) error {
	item, err := s.ByTunnel(ctx, tunnelID)
	if errors.Is(err, ErrDenied) {
		return nil
	}
	if err != nil {
		return err
	}
	if item.TicketID != ticketID {
		return ErrDenied
	}
	_, err = s.rdb.Eval(ctx, `
		redis.call("DEL", KEYS[1])
		for i = 2, #KEYS do
			if redis.call("GET", KEYS[i]) == ARGV[1] then
				redis.call("DEL", KEYS[i])
			end
		end
		return 1
	`, []string{"tunnel:" + tunnelID, "ticket:" + ticketID, "client:" + item.ClientID}, tunnelID).Result()
	return err
}

func (s *Store) RenewTunnel(ctx context.Context, ticketID, tunnelID string) error {
	item, err := s.ByTunnel(ctx, tunnelID)
	if err != nil {
		return err
	}
	if item.TicketID != ticketID {
		return ErrDenied
	}
	ok, err := s.rdb.Eval(ctx, `
		if redis.call("EXISTS", KEYS[1]) == 0 or redis.call("GET", KEYS[2]) ~= ARGV[1] then
			return 0
		end
		redis.call("PEXPIRE", KEYS[1], ARGV[2])
		redis.call("PEXPIRE", KEYS[2], ARGV[2])
		if redis.call("GET", KEYS[3]) == ARGV[1] then
			redis.call("PEXPIRE", KEYS[3], ARGV[2])
		end
		return 1
	`, []string{"tunnel:" + tunnelID, "ticket:" + ticketID, "client:" + item.ClientID}, tunnelID, TTL.Milliseconds()).Int()
	if err == nil && ok == 0 {
		return ErrDenied
	}
	return err
}
