package httpapi

import (
	"errors"
	"net/http"
	"strings"

	"ticket-service/internal/logx"
	"ticket-service/internal/shard"
	"ticket-service/internal/store"
	"ticket-service/internal/token"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
)

type createRequest struct {
	AccountID   string `json:"account_id"`
	Description string `json:"description"`
	Camera      bool   `json:"camera"`
}

func (s *Server) createTicket(c *gin.Context) {
	var req createRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "некорректное тело"})
		return
	}
	accountID, err := uuid.Parse(req.AccountID)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "account_id должен быть uuid"})
		return
	}
	description := strings.TrimSpace(req.Description)
	if description == "" || len(description) > 4000 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "описание пустое или длиннее 4000 символов"})
		return
	}
	rawToken, err := token.New()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "не удалось выдать токен"})
		return
	}
	ticketID := uuid.New()
	shardID := shard.ID(accountID, s.shardCount)
	t := store.Ticket{
		ShardID:     shardID,
		ID:          ticketID,
		AccountID:   accountID,
		Description: description,
		Status:      "queued",
		Camera:      req.Camera,
	}
	if err := s.store.CreateTicket(c.Request.Context(), t, token.Hash(rawToken)); err != nil {
		logx.Error("create ticket", map[string]any{"err": err.Error()})
		c.JSON(http.StatusInternalServerError, gin.H{"error": "не удалось сохранить заявку"})
		return
	}
	logx.Info("ticket created", map[string]any{"ticket_id": ticketID.String(), "shard_id": shardID})
	c.JSON(http.StatusCreated, gin.H{
		"ticket_id":    ticketID.String(),
		"shard_id":     shardID,
		"ticket_token": rawToken,
	})
}

func (s *Server) listTickets(c *gin.Context) {
	items, err := s.store.ListQueued(c.Request.Context(), 50)
	if err != nil {
		logx.Error("list tickets", map[string]any{"err": err.Error()})
		c.JSON(http.StatusInternalServerError, gin.H{"error": "не удалось прочитать очередь"})
		return
	}
	out := make([]gin.H, 0, len(items))
	for _, t := range items {
		out = append(out, ticketJSON(t))
	}
	c.JSON(http.StatusOK, gin.H{"tickets": out})
}

func (s *Server) getTicket(c *gin.Context) {
	ticketID, shardID, ok := s.locate(c)
	if !ok {
		return
	}
	t, err := s.store.Get(c.Request.Context(), shardID, ticketID)
	if err != nil {
		writeStoreErr(c, err)
		return
	}
	c.JSON(http.StatusOK, ticketJSON(t))
}

func (s *Server) claim(c *gin.Context) {
	ticketID, shardID, ok := s.locate(c)
	if !ok {
		return
	}
	operatorID := principalID(c)
	ctx := c.Request.Context()
	t, err := s.store.Get(ctx, shardID, ticketID)
	if err != nil {
		writeStoreErr(c, err)
		return
	}
	tunnelID, err := s.relay.Open(ctx, t.AccountID, ticketID, t.Camera)
	if err != nil {
		logx.Error("relay open", map[string]any{"err": err.Error()})
		c.JSON(http.StatusBadGateway, gin.H{"error": "ретранслятор не принял заявку"})
		return
	}
	if err := s.store.Claim(ctx, shardID, ticketID, operatorID, tunnelID); err != nil {
		_ = s.relay.Close(ctx, ticketID)
		writeStoreErr(c, err)
		return
	}
	logx.Info("ticket claimed", map[string]any{"ticket_id": ticketID.String(), "tunnel_id": tunnelID.String(), "shard_id": shardID})
	c.JSON(http.StatusOK, gin.H{
		"ticket_id":   ticketID.String(),
		"shard_id":    shardID,
		"account_id":  t.AccountID.String(),
		"tunnel_id":   tunnelID.String(),
		"camera":      t.Camera,
		"description": t.Description,
	})
}

func (s *Server) locate(c *gin.Context) (uuid.UUID, int, bool) {
	ticketID, err := uuid.Parse(c.Param("id"))
	if err != nil {
		c.AbortWithStatusJSON(http.StatusBadRequest, gin.H{"error": "id заявки должен быть uuid"})
		return uuid.UUID{}, 0, false
	}
	shardID, err := s.store.Locate(c.Request.Context(), ticketID)
	if err != nil {
		writeStoreErr(c, err)
		c.Abort()
		return uuid.UUID{}, 0, false
	}
	return ticketID, shardID, true
}

func writeStoreErr(c *gin.Context, err error) {
	switch {
	case errors.Is(err, store.ErrNotFound):
		c.JSON(http.StatusNotFound, gin.H{"error": "заявка не найдена"})
	case errors.Is(err, store.ErrConflict):
		c.JSON(http.StatusConflict, gin.H{"error": "заявка в другом состоянии"})
	default:
		logx.Error("store", map[string]any{"err": err.Error()})
		c.JSON(http.StatusInternalServerError, gin.H{"error": "ошибка хранилища"})
	}
}

func ticketJSON(t store.Ticket) gin.H {
	out := gin.H{
		"ticket_id":   t.ID.String(),
		"shard_id":    t.ShardID,
		"account_id":  t.AccountID.String(),
		"description": t.Description,
		"status":      t.Status,
		"camera":      t.Camera,
		"created_at":  t.CreatedAt,
	}
	if t.TunnelID != nil {
		out["tunnel_id"] = t.TunnelID.String()
	}
	return out
}
