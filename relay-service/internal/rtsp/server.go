package rtsp

import (
	"context"
	"net"
	"sync"
	"time"

	"relay-service/internal/logx"
	"relay-service/internal/session"

	"github.com/bluenviron/gortsplib/v4"
	"github.com/bluenviron/gortsplib/v4/pkg/base"
	"github.com/bluenviron/gortsplib/v4/pkg/description"
	"github.com/bluenviron/gortsplib/v4/pkg/format"
	"github.com/pion/rtcp"
	"github.com/pion/rtp"
)

// Server принимает RTSP только с localhost. Снаружи к нему ходят через session.Bridge.
type Server struct {
	addr     string
	sessions *session.Store
	server   *gortsplib.Server
	mu       sync.RWMutex
	streams  map[string]*gortsplib.ServerStream
	pubs     map[string]*gortsplib.ServerSession
}

func New(addr string, sessions *session.Store) *Server {
	s := &Server{
		addr:     addr,
		sessions: sessions,
		streams:  make(map[string]*gortsplib.ServerStream),
		pubs:     make(map[string]*gortsplib.ServerSession),
	}
	s.server = &gortsplib.Server{
		Handler:     s,
		RTSPAddress: addr,
	}
	return s
}

func (s *Server) Start() error {
	return s.server.Start()
}

func (s *Server) Close() {
	s.server.Close()
}

func (s *Server) OnConnOpen(*gortsplib.ServerHandlerOnConnOpenCtx) {}

func (s *Server) OnConnClose(*gortsplib.ServerHandlerOnConnCloseCtx) {}

func (s *Server) OnSessionOpen(*gortsplib.ServerHandlerOnSessionOpenCtx) {}

func (s *Server) OnSessionClose(ctx *gortsplib.ServerHandlerOnSessionCloseCtx) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for path, pub := range s.pubs {
		if ctx.Session == pub {
			if stream, ok := s.streams[path]; ok {
				stream.Close()
				delete(s.streams, path)
			}
			delete(s.pubs, path)
			logx.Info("rtsp session closed", map[string]any{"path": path})
			return
		}
	}
}

func (s *Server) OnDescribe(ctx *gortsplib.ServerHandlerOnDescribeCtx) (*base.Response, *gortsplib.ServerStream, error) {
	if err := s.allow(ctx.Conn.NetConn(), ctx.Path); err != nil {
		return &base.Response{StatusCode: base.StatusUnauthorized}, nil, nil
	}
	// Оператор подключается сразу после захвата, клиент публикует экран чуть позже.
	stream, ok := waitFor(15*time.Second, 200*time.Millisecond, func() (*gortsplib.ServerStream, bool) {
		s.mu.RLock()
		defer s.mu.RUnlock()
		stream, found := s.streams[ctx.Path]
		return stream, found
	})
	if !ok {
		logx.Info("rtsp describe missing", map[string]any{"path": ctx.Path})
		return &base.Response{StatusCode: base.StatusNotFound}, nil, nil
	}
	logx.Info("rtsp describe", map[string]any{"path": ctx.Path})
	return &base.Response{StatusCode: base.StatusOK}, stream, nil
}

func waitFor[T any](timeout, step time.Duration, lookup func() (T, bool)) (T, bool) {
	var zero T
	deadline := time.Now().Add(timeout)
	for {
		if value, ok := lookup(); ok {
			return value, true
		}
		if !time.Now().Before(deadline) {
			return zero, false
		}
		time.Sleep(step)
	}
}

func (s *Server) OnAnnounce(ctx *gortsplib.ServerHandlerOnAnnounceCtx) (*base.Response, error) {
	if err := s.allow(ctx.Conn.NetConn(), ctx.Path); err != nil {
		return &base.Response{StatusCode: base.StatusUnauthorized}, nil
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if stream, ok := s.streams[ctx.Path]; ok {
		stream.Close()
		if pub, ok := s.pubs[ctx.Path]; ok {
			pub.Close()
		}
	}
	stream := &gortsplib.ServerStream{Server: s.server, Desc: ctx.Description}
	if err := stream.Initialize(); err != nil {
		return &base.Response{StatusCode: base.StatusBadRequest}, err
	}
	s.streams[ctx.Path] = stream
	s.pubs[ctx.Path] = ctx.Session
	logx.Info("rtsp announce", map[string]any{"path": ctx.Path})
	return &base.Response{StatusCode: base.StatusOK}, nil
}

func (s *Server) OnSetup(ctx *gortsplib.ServerHandlerOnSetupCtx) (*base.Response, *gortsplib.ServerStream, error) {
	if ctx.Session.State() == gortsplib.ServerSessionStatePreRecord {
		return &base.Response{StatusCode: base.StatusOK}, nil, nil
	}
	if err := s.allow(ctx.Conn.NetConn(), ctx.Path); err != nil {
		return &base.Response{StatusCode: base.StatusUnauthorized}, nil, nil
	}
	s.mu.RLock()
	defer s.mu.RUnlock()
	if stream, ok := s.streams[ctx.Path]; ok {
		return &base.Response{StatusCode: base.StatusOK}, stream, nil
	}
	return &base.Response{StatusCode: base.StatusNotFound}, nil, nil
}

func (s *Server) OnPlay(ctx *gortsplib.ServerHandlerOnPlayCtx) (*base.Response, error) {
	if err := s.allow(ctx.Conn.NetConn(), ctx.Path); err != nil {
		return &base.Response{StatusCode: base.StatusUnauthorized}, nil
	}
	logx.Info("rtsp play", map[string]any{"path": ctx.Path})
	return &base.Response{StatusCode: base.StatusOK}, nil
}

func (s *Server) OnRecord(ctx *gortsplib.ServerHandlerOnRecordCtx) (*base.Response, error) {
	if err := s.allow(ctx.Conn.NetConn(), ctx.Path); err != nil {
		return &base.Response{StatusCode: base.StatusUnauthorized}, nil
	}
	ctx.Session.OnPacketRTPAny(func(medi *description.Media, _ format.Format, pkt *rtp.Packet) {
		s.mu.RLock()
		stream, ok := s.streams[ctx.Path]
		s.mu.RUnlock()
		if ok {
			_ = stream.WritePacketRTP(medi, pkt)
		}
	})
	ctx.Session.OnPacketRTCPAny(func(medi *description.Media, pkt rtcp.Packet) {
		s.mu.RLock()
		stream, ok := s.streams[ctx.Path]
		s.mu.RUnlock()
		if ok {
			stream.WritePacketRTCP(medi, pkt)
		}
	})
	logx.Info("rtsp record", map[string]any{"path": ctx.Path})
	return &base.Response{StatusCode: base.StatusOK}, nil
}

func (s *Server) allow(conn net.Conn, path string) error {
	if !loopback(conn.RemoteAddr()) {
		return session.ErrDenied
	}
	return s.sessions.AllowPath(context.Background(), path)
}

func loopback(addr net.Addr) bool {
	host, _, err := net.SplitHostPort(addr.String())
	if err != nil {
		host = addr.String()
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}
