package rtsp

import (
	"context"
	"net/url"
	"sync"

	"relay-service/internal/logx"
	"relay-service/internal/token"

	"github.com/bluenviron/gortsplib/v4"
	"github.com/bluenviron/gortsplib/v4/pkg/base"
	"github.com/bluenviron/gortsplib/v4/pkg/description"
	"github.com/bluenviron/gortsplib/v4/pkg/format"
	"github.com/pion/rtcp"
	"github.com/pion/rtp"
)

// Server принимает публикацию и чтение на одном RTSP-порту.
// UDP-порты не открываются: RTP едет внутри RTSP.
type Server struct {
	addr    string
	tokens  *token.Checker
	server  *gortsplib.Server
	mu      sync.RWMutex
	streams map[string]*gortsplib.ServerStream
	pubs    map[string]*gortsplib.ServerSession
}

func New(addr string, tokens *token.Checker) *Server {
	s := &Server{
		addr:    addr,
		tokens:  tokens,
		streams: make(map[string]*gortsplib.ServerStream),
		pubs:    make(map[string]*gortsplib.ServerSession),
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

func (s *Server) Paths() []string {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]string, 0, len(s.streams))
	for path := range s.streams {
		out = append(out, path)
	}
	return out
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
	if err := s.tokens.Allow(context.Background(), queryToken(ctx.Query), "reader", ctx.Path); err != nil {
		return &base.Response{StatusCode: base.StatusUnauthorized}, nil, nil
	}
	s.mu.RLock()
	defer s.mu.RUnlock()
	if stream, ok := s.streams[ctx.Path]; ok {
		return &base.Response{StatusCode: base.StatusOK}, stream, nil
	}
	return &base.Response{StatusCode: base.StatusNotFound}, nil, nil
}

func (s *Server) OnAnnounce(ctx *gortsplib.ServerHandlerOnAnnounceCtx) (*base.Response, error) {
	if err := s.tokens.Allow(context.Background(), queryToken(ctx.Query), "publisher", ctx.Path); err != nil {
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
	if err := s.tokens.Allow(context.Background(), queryToken(ctx.Query), "reader", ctx.Path); err != nil {
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
	if err := s.tokens.Allow(context.Background(), queryToken(ctx.Query), "reader", ctx.Path); err != nil {
		return &base.Response{StatusCode: base.StatusUnauthorized}, nil
	}
	logx.Info("rtsp play", map[string]any{"path": ctx.Path})
	return &base.Response{StatusCode: base.StatusOK}, nil
}

func (s *Server) OnRecord(ctx *gortsplib.ServerHandlerOnRecordCtx) (*base.Response, error) {
	if err := s.tokens.Allow(context.Background(), queryToken(ctx.Query), "publisher", ctx.Path); err != nil {
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

func queryToken(q string) string {
	values, _ := url.ParseQuery(q)
	return values.Get("token")
}
