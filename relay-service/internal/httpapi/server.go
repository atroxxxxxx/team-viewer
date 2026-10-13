package httpapi

import (
	"crypto/subtle"
	"net/http"
	"time"

	"relay-service/internal/logx"
	"relay-service/internal/session"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
)

type Server struct {
	sessions *session.Store
	token    string
}

func New(sessions *session.Store, token string) *Server {
	return &Server{sessions: sessions, token: token}
}

func (s *Server) Router() *gin.Engine {
	gin.SetMode(gin.ReleaseMode)
	r := gin.New()
	r.Use(gin.Recovery(), accessLog)
	r.GET("/health", s.health)
	api := r.Group("/api/v1", s.requireIngest)
	api.POST("/tunnels", s.open)
	api.DELETE("/tunnels/:ticket", s.close)
	return r
}

type openRequest struct {
	ClientID string `json:"client_id"`
	TicketID string `json:"ticket_id"`
	Camera   bool   `json:"camera"`
}

func (s *Server) open(c *gin.Context) {
	var req openRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "некорректное тело"})
		return
	}
	clientID, err := uuid.Parse(req.ClientID)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "client_id должен быть uuid"})
		return
	}
	ticketID, err := uuid.Parse(req.TicketID)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "ticket_id должен быть uuid"})
		return
	}
	item := session.Tunnel{
		TunnelID: uuid.NewString(),
		ClientID: clientID.String(),
		TicketID: ticketID.String(),
		Camera:   req.Camera,
	}
	if err := s.sessions.Put(c.Request.Context(), item); err != nil {
		logx.Error("redis put", map[string]any{"err": err.Error()})
		c.JSON(http.StatusInternalServerError, gin.H{"error": "не удалось сохранить туннель"})
		return
	}
	logx.Info("tunnel opened", map[string]any{"tunnel_id": item.TunnelID, "ticket_id": item.TicketID})
	c.JSON(http.StatusCreated, gin.H{"tunnel_id": item.TunnelID})
}

func (s *Server) close(c *gin.Context) {
	ticketID, err := uuid.Parse(c.Param("ticket"))
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "ticket должен быть uuid"})
		return
	}
	if err := s.sessions.DeleteByTicket(c.Request.Context(), ticketID.String()); err != nil {
		logx.Error("redis delete", map[string]any{"err": err.Error()})
		c.JSON(http.StatusInternalServerError, gin.H{"error": "не удалось закрыть туннель"})
		return
	}
	c.Status(http.StatusNoContent)
}

func (s *Server) health(c *gin.Context) {
	if err := s.sessions.Ping(c.Request.Context()); err != nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"status": "redis down"})
		return
	}
	c.JSON(http.StatusOK, gin.H{"status": "ok"})
}

func (s *Server) requireIngest(c *gin.Context) {
	got := c.GetHeader("Authorization")
	want := "Bearer " + s.token
	if s.token == "" || subtle.ConstantTimeCompare([]byte(got), []byte(want)) != 1 {
		c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "нужен токен приёма заявок"})
	}
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
