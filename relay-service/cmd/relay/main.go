package main

import (
	"context"
	"embed"
	"os"
	"os/signal"
	"syscall"
	"time"

	"relay-service/internal/auth"
	"relay-service/internal/httpapi"
	"relay-service/internal/logx"
	"relay-service/internal/rtsp"
	"relay-service/internal/token"
)

//go:embed openapi.json
var specFS embed.FS

func main() {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	creds, err := auth.New(env("CLIENT_USER", ""), env("CLIENT_PASSWORD", ""), env("OPERATOR_USER", ""), env("OPERATOR_PASSWORD", ""))
	if err != nil {
		logx.Error("auth", map[string]any{"err": err.Error()})
		os.Exit(1)
	}
	tokens := token.Open(env("REDIS_ADDR", "127.0.0.1:6379"))
	if err := tokens.Ping(ctx); err != nil {
		logx.Error("redis", map[string]any{"err": err.Error()})
		os.Exit(1)
	}
	streams := rtsp.New(env("RTSP_ADDR", ":8554"), tokens)
	if err := streams.Start(); err != nil {
		logx.Error("rtsp", map[string]any{"err": err.Error()})
		os.Exit(1)
	}
	defer streams.Close()

	spec, err := specFS.ReadFile("openapi.json")
	if err != nil {
		logx.Error("openapi", map[string]any{"err": err.Error()})
		os.Exit(1)
	}
	addr := env("HTTP_ADDR", "127.0.0.1:18081")
	httpSrv := httpapi.New(tokens, streams, creds, spec, env("SWAGGER_DOC_URL", "/swagger/doc.json"))
	errCh := make(chan error, 1)
	go func() {
		logx.Info("listen", map[string]any{"http": addr, "rtsp": env("RTSP_ADDR", ":8554")})
		errCh <- httpSrv.Router().Run(addr)
	}()

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	select {
	case err := <-errCh:
		logx.Error("http", map[string]any{"err": err.Error()})
		os.Exit(1)
	case <-stop:
		logx.Info("stop", nil)
	}
}

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
