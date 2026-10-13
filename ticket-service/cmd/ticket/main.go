package main

import (
	"context"
	"embed"
	"os"
	"strconv"
	"time"

	"ticket-service/internal/auth"
	"ticket-service/internal/httpapi"
	"ticket-service/internal/logx"
	"ticket-service/internal/relayclient"
	"ticket-service/internal/store"
)

//go:embed openapi.json
var specFS embed.FS

func main() {
	creds, err := auth.New(env("CLIENT_USER", ""), env("CLIENT_PASSWORD", ""), env("OPERATOR_USER", ""), env("OPERATOR_PASSWORD", ""))
	if err != nil {
		logx.Error("auth", map[string]any{"err": err.Error()})
		os.Exit(1)
	}
	shardCount, _ := strconv.Atoi(env("SHARD_COUNT", "4"))
	if shardCount <= 0 {
		logx.Error("shard count", map[string]any{"config": shardCount})
		os.Exit(1)
	}
	st := waitDB(env("DATABASE_URL", "postgres://support:support@127.0.0.1:5432/support?sslmode=disable"), shardCount)
	defer st.Close()

	spec, err := specFS.ReadFile("openapi.json")
	if err != nil {
		logx.Error("openapi", map[string]any{"err": err.Error()})
		os.Exit(1)
	}
	relay := relayclient.New(env("RELAY_INGEST_URL", "http://127.0.0.1:18082"), env("RELAY_INGEST_TOKEN", ""))
	srv := httpapi.New(st, relay, creds, shardCount, spec, env("SWAGGER_DOC_URL", "/swagger/doc.json"))
	addr := env("HTTP_ADDR", "127.0.0.1:18080")
	logx.Info("listen", map[string]any{"addr": addr})
	if err := srv.Router().Run(addr); err != nil {
		logx.Error("http", map[string]any{"err": err.Error()})
		os.Exit(1)
	}
}

func waitDB(dsn string, shardCount int) *store.Store {
	deadline := time.Now().Add(60 * time.Second)
	var last error
	for {
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		st, err := store.Open(ctx, dsn)
		if err == nil {
			local, countErr := st.LocalShardCount(ctx)
			if countErr == nil && local == shardCount {
				cancel()
				return st
			}
			if countErr != nil {
				last = countErr
			} else {
				last = shardMismatch{local, shardCount}
			}
			st.Close()
		} else {
			last = err
		}
		cancel()
		if time.Now().After(deadline) {
			logx.Error("dependencies", map[string]any{"err": last.Error()})
			os.Exit(1)
		}
		logx.Error("dependencies retry", map[string]any{"err": last.Error()})
		time.Sleep(time.Second)
	}
}

type shardMismatch struct{ local, config int }

func (e shardMismatch) Error() string {
	return "shard count mismatch"
}

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
