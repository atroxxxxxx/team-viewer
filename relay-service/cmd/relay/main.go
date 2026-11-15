package main

import (
	"context"
	"crypto/tls"
	"net"
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

	sessionAddr := env("SESSION_ADDR", "127.0.0.1:8554")
	bridge := session.NewBridge(sessionAddr, rtspAddr, sessions)
	cert, key := os.Getenv("SESSION_TLS_CERT"), os.Getenv("SESSION_TLS_KEY")
	if cert != "" || key != "" {
		certificate, err := tls.LoadX509KeyPair(cert, key)
		if err != nil {
			logx.Error("session TLS", map[string]any{"err": err.Error()})
			os.Exit(1)
		}
		bridge.TLS = &tls.Config{Certificates: []tls.Certificate{certificate}, MinVersion: tls.VersionTLS12}
	} else if os.Getenv("SESSION_LOCAL_PLAINTEXT") != "1" {
		host, _, _ := net.SplitHostPort(sessionAddr)
		if ip := net.ParseIP(host); ip == nil || !ip.IsLoopback() {
			logx.Error("session TLS required for non-loopback listener", nil)
			os.Exit(1)
		}
	}
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
		logx.Info("listen", map[string]any{"ingest": addr, "session": sessionAddr, "rtsp": rtspAddr})
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
