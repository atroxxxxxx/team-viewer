package httpapi

import (
	"errors"
	"net/http"
	"strings"

	"ticket-service/internal/cache"
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

type mediaRequest struct {
	Screen string `json:"screen"`
	Camera string `json:"camera"`
}

type claimRequest struct {
	OperatorID string `json:"operator_id"`
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
	paths := []string{"/sessions/" + ticketID.String() + "/screen"}
	if req.Camera {
		paths = append(paths, "/sessions/"+ticketID.String()+"/camera")
	}
	grants := make([]store.Grant, 0, len(paths))
	for _, path := range paths {
		grants = append(grants, store.Grant{Path: path, Hash: token.Hash(rawToken), Role: "publisher"})
	}
	t := store.Ticket{
		ShardID:     shardID,
		ID:          ticketID,
		AccountID:   accountID,
		Description: description,
		Status:      "created",
		Camera:      req.Camera,
	}
	ctx := c.Request.Context()
	if err := s.store.CreateTicket(ctx, t, grants, s.tokenTTL); err != nil {
		logx.Error("create ticket", map[string]any{"err": err.Error()})
		c.JSON(http.StatusInternalServerError, gin.H{"error": "не удалось сохранить заявку"})
		return
	}
	if err := s.cache.PutMediaToken(ctx, token.Hash(rawToken), cache.MediaToken{
		Role: "publisher", Paths: paths, TicketID: ticketID.String(), ShardID: shardID,
	}, s.tokenTTL); err != nil {
		logx.Error("cache publisher token", map[string]any{"err": err.Error()})
		c.JSON(http.StatusInternalServerError, gin.H{"error": "не удалось записать токен"})
		return
	}
	_ = s.cache.SaveTicket(ctx, ticketID.String(), map[string]string{
		"status": "created", "shard_id": itoa(shardID), "camera": boolText(req.Camera),
	})
	logx.Info("ticket created", map[string]any{"ticket_id": ticketID.String(), "shard_id": shardID})
	c.JSON(http.StatusCreated, gin.H{
		"ticket_id":       ticketID.String(),
		"shard_id":        shardID,
		"publisher_token": rawToken,
		"paths":           paths,
		"rtsp_base":       "rtsp://127.0.0.1:8554",
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

func (s *Server) mediaUp(c *gin.Context) {
	ticketID, shardID, ok := s.locate(c)
	if !ok {
		return
	}
	var req mediaRequest
	if err := c.ShouldBindJSON(&req); err != nil || req.Screen != "up" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "screen должен быть up"})
		return
	}
	ctx := c.Request.Context()
	if err := s.store.MarkQueued(ctx, shardID, ticketID); err != nil {
		writeStoreErr(c, err)
		return
	}
	_ = s.cache.SaveTicket(ctx, ticketID.String(), map[string]string{"status": "queued", "shard_id": itoa(shardID)})
	_ = s.cache.AddPresence(ctx, ticketID.String(), "client")
	_ = s.cache.Publish(ctx, "queue:tickets", ticketID.String())
	c.JSON(http.StatusOK, gin.H{"status": "queued"})
}

func (s *Server) claim(c *gin.Context) {
	ticketID, shardID, ok := s.locate(c)
	if !ok {
		return
	}
	operatorID := principalID(c)
	ctx := c.Request.Context()
	locked, err := s.cache.Claim(ctx, ticketID.String(), operatorID.String(), s.lockTTL)
	if err != nil {
		logx.Error("claim lock", map[string]any{"err": err.Error()})
		c.JSON(http.StatusInternalServerError, gin.H{"error": "не удалось захватить заявку"})
		return
	}
	if !locked {
		c.JSON(http.StatusConflict, gin.H{"error": "заявка уже занята"})
		return
	}
	t, err := s.store.Get(ctx, shardID, ticketID)
	if err != nil {
		_ = s.cache.Release(ctx, ticketID.String())
		writeStoreErr(c, err)
		return
	}
	rawToken, err := token.New()
	if err != nil {
		_ = s.cache.Release(ctx, ticketID.String())
		c.JSON(http.StatusInternalServerError, gin.H{"error": "не удалось выдать токен"})
		return
	}
	paths := []string{"/sessions/" + ticketID.String() + "/screen"}
	if t.Camera {
		paths = append(paths, "/sessions/"+ticketID.String()+"/camera")
	}
	grants := make([]store.Grant, 0, len(paths))
	for _, path := range paths {
		grants = append(grants, store.Grant{Path: path, Hash: token.Hash(rawToken), Role: "reader"})
	}
	if err := s.store.Claim(ctx, shardID, ticketID, operatorID, grants, s.tokenTTL); err != nil {
		_ = s.cache.Release(ctx, ticketID.String())
		writeStoreErr(c, err)
		return
	}
	if err := s.cache.PutMediaToken(ctx, token.Hash(rawToken), cache.MediaToken{
		Role: "reader", Paths: paths, TicketID: ticketID.String(), ShardID: shardID,
	}, s.tokenTTL); err != nil {
		logx.Error("cache reader token", map[string]any{"err": err.Error()})
		c.JSON(http.StatusInternalServerError, gin.H{"error": "не удалось записать токен"})
		return
	}
	_ = s.cache.AddPresence(ctx, ticketID.String(), "operator")
	_ = s.cache.SaveTicket(ctx, ticketID.String(), map[string]string{"status": "assigned", "shard_id": itoa(shardID)})
	logx.Info("ticket claimed", map[string]any{"ticket_id": ticketID.String(), "shard_id": shardID})
	c.JSON(http.StatusOK, gin.H{
		"ticket_id":    ticketID.String(),
		"shard_id":     shardID,
		"reader_token": rawToken,
		"paths":        paths,
		"rtsp_base":    "rtsp://127.0.0.1:8554",
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
	return gin.H{
		"ticket_id":   t.ID.String(),
		"shard_id":    t.ShardID,
		"account_id":  t.AccountID.String(),
		"description": t.Description,
		"status":      t.Status,
		"camera":      t.Camera,
		"created_at":  t.CreatedAt,
	}
}

func boolText(v bool) string {
	if v {
		return "true"
	}
	return "false"
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var buf [4]byte
	i := len(buf)
	for n > 0 {
		i--
		buf[i] = byte('0' + n%10)
		n /= 10
	}
	return string(buf[i:])
}
