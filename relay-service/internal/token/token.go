package token

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"

	"github.com/redis/go-redis/v9"
)

var ErrDenied = errors.New("denied")

type Grant struct {
	Role     string   `json:"role"`
	Paths    []string `json:"paths"`
	TicketID string   `json:"ticket_id"`
	ShardID  int      `json:"shard_id"`
}

func Allows(g Grant, role, path string) bool {
	if g.Role != role {
		return false
	}
	for _, item := range g.Paths {
		if item == path {
			return true
		}
	}
	return false
}

type Checker struct {
	rdb *redis.Client
}

func Open(addr string) *Checker {
	return &Checker{rdb: redis.NewClient(&redis.Options{Addr: addr})}
}

func (c *Checker) Ping(ctx context.Context) error {
	return c.rdb.Ping(ctx).Err()
}

func (c *Checker) Allow(ctx context.Context, rawToken, role, path string) error {
	if rawToken == "" {
		return ErrDenied
	}
	sum := sha256.Sum256([]byte(rawToken))
	key := "media:token:" + hex.EncodeToString(sum[:])
	raw, err := c.rdb.Get(ctx, key).Bytes()
	if err != nil {
		return ErrDenied
	}
	var grant Grant
	if err := json.Unmarshal(raw, &grant); err != nil {
		return ErrDenied
	}
	if !Allows(grant, role, path) {
		return ErrDenied
	}
	return nil
}
