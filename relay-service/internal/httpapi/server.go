package httpapi

import (
	"net"
	"net/http"
	"strings"
	"time"

	"relay-service/internal/auth"
	"relay-service/internal/logx"
	"relay-service/internal/rtsp"
	"relay-service/internal/token"

	"github.com/gin-gonic/gin"
	swaggerFiles "github.com/swaggo/files"
	ginSwagger "github.com/swaggo/gin-swagger"
)

type Server struct {
	tokens     *token.Checker
	rtsp       *rtsp.Server
	creds      auth.Checker
	spec       []byte
	swaggerDoc string
}

func New(tokens *token.Checker, streams *rtsp.Server, creds auth.Checker, spec []byte, swaggerDoc string) *Server {
	return &Server{tokens: tokens, rtsp: streams, creds: creds, spec: spec, swaggerDoc: swaggerDoc}
}

func (s *Server) Router() *gin.Engine {
	gin.SetMode(gin.ReleaseMode)
	r := gin.New()
	r.Use(gin.Recovery(), accessLog)
	r.GET("/", localRoot("/swagger/index.html"))
	r.GET("/health", s.health)
	r.GET("/api/v1/paths", s.requireOperator, s.paths)
	r.GET("/openapi.json", s.specJSON)
	r.GET("/openapi/relay.json", s.specJSON)
	r.GET("/swagger/*any", ginSwagger.WrapHandler(swaggerFiles.Handler, ginSwagger.URL(s.swaggerDoc)))
	return r
}

func (s *Server) health(c *gin.Context) {
	if err := s.tokens.Ping(c.Request.Context()); err != nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"status": "redis down"})
		return
	}
	c.JSON(http.StatusOK, gin.H{"status": "ok"})
}

func (s *Server) requireOperator(c *gin.Context) {
	user, pass, ok := c.Request.BasicAuth()
	principal, allowed := s.creds.Authenticate(user, pass, ok)
	if !allowed || principal.Role != auth.RoleOperator {
		c.Header("WWW-Authenticate", `Basic realm="relay"`)
		c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "нужна аутентификация оператора"})
	}
}

func (s *Server) paths(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{"paths": s.rtsp.Paths()})
}

func (s *Server) specJSON(c *gin.Context) {
	c.Data(http.StatusOK, "application/json", s.spec)
}

func localRoot(target string) gin.HandlerFunc {
	return func(c *gin.Context) {
		if !requestedFromLocalhost(c.Request) {
			drop(c)
			return
		}
		c.Redirect(http.StatusFound, target)
	}
}

func requestedFromLocalhost(r *http.Request) bool {
	host := r.Host
	if name, _, err := net.SplitHostPort(host); err == nil {
		host = name
	}
	host = strings.Trim(host, "[]")
	if host == "localhost" || host == "127.0.0.1" || host == "::1" {
		return true
	}
	return loopback(r.RemoteAddr)
}

func loopback(remote string) bool {
	host, _, err := net.SplitHostPort(remote)
	if err != nil {
		host = remote
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

func drop(c *gin.Context) {
	conn, _, err := c.Writer.Hijack()
	if err != nil {
		c.Abort()
		return
	}
	_ = conn.Close()
	c.Abort()
}

func accessLog(c *gin.Context) {
	start := time.Now()
	c.Next()
	logx.Info("http", map[string]any{
		"method": c.Request.Method,
		"path":   c.Request.URL.Path,
		"status": c.Writer.Status(),
		"ms":     time.Since(start).Milliseconds(),
	})
}
