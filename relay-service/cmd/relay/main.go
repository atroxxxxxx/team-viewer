package main

import (
	"context"
	"os"
	"os/signal"
	"syscall"
	"time"

	"relay-service/internal/httpapi"
	"relay-service/internal/logx"
	"relay-service/internal/rtsp"
	"relay-service/internal/session"
)

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	pingCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	sessions := session.Open(env("REDIS_ADDR", "127.0.0.1:6379"))
	err := sessions.Ping(pingCtx)
	cancel()
	if err != nil {
		logx.Error("redis", map[string]any{"err": err.Error()})
		os.Exit(1)
	}

	rtspAddr := env("RTSP_ADDR", "127.0.0.1:18554")
	streams := rtsp.New(rtspAddr, sessions)
	if err := streams.Start(); err != nil {
		logx.Error("rtsp", map[string]any{"err": err.Error()})
		os.Exit(1)
	}
	defer streams.Close()

	bridge := session.NewBridge(env("SESSION_ADDR", ":8554"), rtspAddr, sessions)
	go func() {
		if err := bridge.Serve(ctx); err != nil {
			logx.Error("session", map[string]any{"err": err.Error()})
			os.Exit(1)
		}
	}()

	addr := env("INGEST_ADDR", ":18082")
	httpSrv := httpapi.New(sessions, env("RELAY_INGEST_TOKEN", ""))
	errCh := make(chan error, 1)
	go func() {
		logx.Info("listen", map[string]any{"ingest": addr, "session": env("SESSION_ADDR", ":8554"), "rtsp": rtspAddr})
		errCh <- httpSrv.Router().Run(addr)
	}()
	select {
	case err := <-errCh:
		logx.Error("http", map[string]any{"err": err.Error()})
		os.Exit(1)
	case <-ctx.Done():
		logx.Info("stop", nil)
	}
}

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
