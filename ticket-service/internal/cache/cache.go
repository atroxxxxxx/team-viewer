package cache

import (
	"context"
	"encoding/json"
	"time"

	"github.com/redis/go-redis/v9"
)

type Cache struct {
	rdb *redis.Client
}

func Open(addr string) *Cache {
	return &Cache{rdb: redis.NewClient(&redis.Options{Addr: addr})}
}

func (c *Cache) Ping(ctx context.Context) error {
	return c.rdb.Ping(ctx).Err()
}

func (c *Cache) SaveTicket(ctx context.Context, id string, fields map[string]string) error {
	return c.rdb.HSet(ctx, "ticket:"+id, fields).Err()
}

func (c *Cache) Publish(ctx context.Context, channel, payload string) error {
	return c.rdb.Publish(ctx, channel, payload).Err()
}

func (c *Cache) AddPresence(ctx context.Context, ticketID, member string) error {
	return c.rdb.SAdd(ctx, "presence:ticket:"+ticketID, member).Err()
}

type MediaToken struct {
	Role     string   `json:"role"`
	Paths    []string `json:"paths"`
	TicketID string   `json:"ticket_id"`
	ShardID  int      `json:"shard_id"`
}

func (c *Cache) PutMediaToken(ctx context.Context, hash string, grant MediaToken, ttl time.Duration) error {
	raw, err := json.Marshal(grant)
	if err != nil {
		return err
	}
	pipe := c.rdb.TxPipeline()
	pipe.Set(ctx, "media:token:"+hash, raw, ttl)
	pipe.SAdd(ctx, "media:hashes:"+grant.TicketID, hash)
	pipe.Expire(ctx, "media:hashes:"+grant.TicketID, ttl)
	_, err = pipe.Exec(ctx)
	return err
}

func (c *Cache) RevokeTicketMedia(ctx context.Context, ticketID string) error {
	key := "media:hashes:" + ticketID
	hashes, err := c.rdb.SMembers(ctx, key).Result()
	if err != nil {
		return err
	}
	keys := make([]string, 0, len(hashes)+2)
	for _, hash := range hashes {
		keys = append(keys, "media:token:"+hash)
	}
	keys = append(keys, key, "lock:ticket:"+ticketID)
	return c.rdb.Del(ctx, keys...).Err()
}

func (c *Cache) Claim(ctx context.Context, ticketID, operatorID string, ttl time.Duration) (bool, error) {
	return c.rdb.SetNX(ctx, "lock:ticket:"+ticketID, operatorID, ttl).Result()
}

func (c *Cache) Release(ctx context.Context, ticketID string) error {
	return c.rdb.Del(ctx, "lock:ticket:"+ticketID).Err()
}
