package httpapi

import (
	"net"
	"net/http"
	"strings"
	"time"

	"ticket-service/internal/auth"
	"ticket-service/internal/cache"
	"ticket-service/internal/logx"
	"ticket-service/internal/store"

	"github.com/gin-gonic/gin"
	swaggerFiles "github.com/swaggo/files"
	ginSwagger "github.com/swaggo/gin-swagger"
)

type Server struct {
	store      *store.Store
	cache      *cache.Cache
	creds      auth.Checker
	shardCount int
	tokenTTL   time.Duration
	lockTTL    time.Duration
	swaggerDoc string
	spec       []byte
}

func New(st *store.Store, ca *cache.Cache, creds auth.Checker, shardCount int, spec []byte, swaggerDoc string) *Server {
	return &Server{
		store:      st,
		cache:      ca,
		creds:      creds,
		shardCount: shardCount,
		tokenTTL:   12 * time.Hour,
		lockTTL:    8 * time.Hour,
		swaggerDoc: swaggerDoc,
		spec:       spec,
	}
}

func (s *Server) Router() *gin.Engine {
	gin.SetMode(gin.ReleaseMode)
	r := gin.New()
	r.Use(gin.Recovery(), accessLog, limitBody)
	r.GET("/", localRoot("/swagger/index.html"))
	r.GET("/health", s.health)
	r.GET("/openapi.json", s.specJSON)
	r.GET("/openapi/tickets.json", s.specJSON)
	r.GET("/swagger/*any", ginSwagger.WrapHandler(swaggerFiles.Handler, ginSwagger.URL(s.swaggerDoc)))
	api := r.Group("/api/v1", s.requireAuth)
	api.POST("/tickets", s.only(auth.RoleClient), s.createTicket)
	api.GET("/tickets", s.only(auth.RoleOperator), s.listTickets)
	api.GET("/tickets/:id", s.reader(), s.getTicket)
	api.POST("/tickets/:id/media", s.clientToken(), s.mediaUp)
	api.POST("/tickets/:id/claim", s.only(auth.RoleOperator), s.claim)
	api.GET("/tickets/:id/session", s.reader(), s.getSession)
	api.POST("/tickets/:id/messages", s.participant(), s.postMessage)
	api.POST("/tickets/:id/control-requests", s.assignee(), s.requestControl)
	api.POST("/tickets/:id/control-requests/:grant/decision", s.clientToken(), s.decideControl)
	api.POST("/tickets/:id/input", s.assignee(), s.postInput)
	api.POST("/tickets/:id/input/:action/result", s.clientToken(), s.finishInput)
	api.POST("/tickets/:id/actions", s.assignee(), s.postBlocked)
	api.POST("/tickets/:id/plans", s.assignee(), s.postPlan)
	api.POST("/tickets/:id/steps/:step/observations", s.clientToken(), s.postObservation)
	api.POST("/tickets/:id/steps/:step/complete", s.assignee(), s.completeStep)
	api.POST("/tickets/:id/steps/:step/revise", s.assignee(), s.reviseStep)
	api.POST("/tickets/:id/problems", s.assignee(), s.postProblem)
	api.POST("/tickets/:id/resolution", s.participant(), s.postResolution)
	return r
}

func limitBody(c *gin.Context) {
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, 1<<20)
	c.Next()
}

func (s *Server) health(c *gin.Context) {
	ctx := c.Request.Context()
	if err := s.cache.Ping(ctx); err != nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"status": "redis down"})
		return
	}
	c.JSON(http.StatusOK, gin.H{"status": "ok"})
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
