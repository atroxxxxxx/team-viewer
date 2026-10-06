package httpapi

import (
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"time"

	"ticket-service/internal/policy"
	"ticket-service/internal/store"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
)

type messageRequest struct {
	Author string `json:"author"`
	Body   string `json:"body"`
}

type controlRequest struct {
	Intent string `json:"intent"`
}

type decisionRequest struct {
	Allow bool   `json:"allow"`
	Scope string `json:"scope"`
}

type inputRequest struct {
	GrantID string         `json:"grant_id"`
	Type    string         `json:"type"`
	Intent  string         `json:"intent"`
	Pointer map[string]any `json:"pointer"`
	Key     map[string]any `json:"key"`
	Text    string         `json:"text"`
}

type resultRequest struct {
	Phase       string `json:"phase"`
	Observation string `json:"observation"`
}

type blockedRequest struct {
	Actor  string `json:"actor"`
	Intent string `json:"intent"`
	Detail string `json:"detail"`
}

type planRequest struct {
	Steps []string `json:"steps"`
}

type observationRequest struct {
	Body string `json:"body"`
}

type completeRequest struct {
	SeenOnScreen bool   `json:"seen_on_screen"`
	Body         string `json:"body"`
}

type problemRequest struct {
	ParentStepID string   `json:"parent_step_id"`
	Description  string   `json:"description"`
	Steps        []string `json:"steps"`
}

type resolutionRequest struct {
	Role    string `json:"role"`
	Accept  bool   `json:"accept"`
	Comment string `json:"comment"`
}

func (s *Server) postMessage(c *gin.Context) {
	ticketID, shardID, ok := s.locate(c)
	if !ok {
		return
	}
	var req messageRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "некорректное тело"})
		return
	}
	body := strings.TrimSpace(req.Body)
	if body == "" || len(body) > 4000 || !policy.TextAllowed(body) {
		c.JSON(http.StatusBadRequest, gin.H{"error": "сообщение пустое, слишком длинное или запрещено правилами"})
		return
	}
	if err := s.store.AddMessage(c.Request.Context(), shardID, ticketID, principal(c).Role, body); err != nil {
		writeStoreErr(c, err)
		return
	}
	c.JSON(http.StatusCreated, gin.H{"status": "sent"})
}

func (s *Server) requestControl(c *gin.Context) {
	ticketID, shardID, ok := s.locate(c)
	if !ok {
		return
	}
	var req controlRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		req.Intent = "request_control"
	}
	if !policy.IntentAllowed(req.Intent) {
		c.JSON(http.StatusForbidden, gin.H{"error": "намерение запрещено"})
		return
	}
	grantID, err := s.store.RequestControl(c.Request.Context(), shardID, ticketID, req.Intent)
	if err != nil {
		writeStoreErr(c, err)
		return
	}
	c.JSON(http.StatusCreated, gin.H{"grant_id": grantID.String(), "status": "pending"})
}

func (s *Server) decideControl(c *gin.Context) {
	ticketID, shardID, ok := s.locate(c)
	if !ok {
		return
	}
	grantID, err := uuid.Parse(c.Param("grant"))
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "grant должен быть uuid"})
		return
	}
	var req decisionRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "некорректное тело"})
		return
	}
	scope := "pointer,key,text"
	if req.Allow && strings.TrimSpace(req.Scope) != "" {
		normalized, allowed := policy.NormalizeScope(req.Scope)
		if !allowed {
			c.JSON(http.StatusForbidden, gin.H{"error": "scope может содержать только pointer, key и text"})
			return
		}
		scope = normalized
	}
	if err := s.store.DecideControl(c.Request.Context(), shardID, ticketID, grantID, req.Allow, scope, 30*time.Minute); err != nil {
		writeStoreErr(c, err)
		return
	}
	c.JSON(http.StatusOK, gin.H{"allow": req.Allow, "scope": scope})
}

func (s *Server) postInput(c *gin.Context) {
	ticketID, shardID, ok := s.locate(c)
	if !ok {
		return
	}
	var req inputRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "некорректное тело"})
		return
	}
	grantID, err := uuid.Parse(req.GrantID)
	if err != nil || (req.Type != "pointer" && req.Type != "key" && req.Type != "text") || !policy.IntentAllowed(req.Intent) || !policy.TextAllowed(req.Text) || len(req.Text) > 200 {
		c.JSON(http.StatusForbidden, gin.H{"error": "событие не проходит проверку схемы или правил"})
		return
	}
	grant, err := s.store.ActiveGrant(c.Request.Context(), shardID, ticketID)
	if err != nil {
		writeStoreErr(c, err)
		return
	}
	if !policy.ScopeAllows(grant.Scope, req.Type) {
		c.JSON(http.StatusForbidden, gin.H{"error": "тип события вне scope"})
		return
	}
	detail, _ := json.Marshal(req)
	actionID, err := s.store.ProposeInput(c.Request.Context(), shardID, ticketID, grantID, req.Intent, detail)
	if err != nil {
		writeStoreErr(c, err)
		return
	}
	c.JSON(http.StatusCreated, gin.H{"action_id": actionID.String(), "phase": "proposed"})
}

func (s *Server) finishInput(c *gin.Context) {
	ticketID, shardID, ok := s.locate(c)
	if !ok {
		return
	}
	actionID, err := uuid.Parse(c.Param("action"))
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "action должен быть uuid"})
		return
	}
	var req resultRequest
	if err := c.ShouldBindJSON(&req); err != nil || (req.Phase != "applied" && req.Phase != "rejected_by_client") {
		c.JSON(http.StatusBadRequest, gin.H{"error": "phase должна быть applied или rejected_by_client"})
		return
	}
	if err := s.store.FinishInput(c.Request.Context(), shardID, ticketID, actionID, req.Phase, strings.TrimSpace(req.Observation)); err != nil {
		writeStoreErr(c, err)
		return
	}
	c.JSON(http.StatusOK, gin.H{"phase": req.Phase})
}

func (s *Server) postBlocked(c *gin.Context) {
	ticketID, shardID, ok := s.locate(c)
	if !ok {
		return
	}
	var req blockedRequest
	if err := c.ShouldBindJSON(&req); err != nil || strings.TrimSpace(req.Intent) == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "нужно намерение"})
		return
	}
	detail, _ := json.Marshal(map[string]string{"detail": req.Detail})
	if err := s.store.LogBlocked(c.Request.Context(), shardID, ticketID, principal(c).Role, req.Intent, detail); err != nil {
		writeStoreErr(c, err)
		return
	}
	c.JSON(http.StatusCreated, gin.H{"phase": "blocked_by_policy"})
}

func (s *Server) postPlan(c *gin.Context) {
	ticketID, shardID, ok := s.locate(c)
	if !ok {
		return
	}
	var req planRequest
	if err := c.ShouldBindJSON(&req); err != nil || len(req.Steps) == 0 || len(req.Steps) > 20 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "нужен хотя бы один шаг и не больше 20"})
		return
	}
	steps := make([]string, 0, len(req.Steps))
	for _, step := range req.Steps {
		step = strings.TrimSpace(step)
		if step == "" || len(step) > 500 || !policy.TextAllowed(step) {
			c.JSON(http.StatusForbidden, gin.H{"error": "шаг пустой или запрещён правилами"})
			return
		}
		steps = append(steps, step)
	}
	planID, err := s.store.CreatePlan(c.Request.Context(), shardID, ticketID, nil, steps)
	if err != nil {
		writeStoreErr(c, err)
		return
	}
	c.JSON(http.StatusCreated, gin.H{"plan_id": planID.String()})
}

func (s *Server) postObservation(c *gin.Context) {
	ticketID, shardID, ok := s.locate(c)
	if !ok {
		return
	}
	stepID, err := uuid.Parse(c.Param("step"))
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "step должен быть uuid"})
		return
	}
	var req observationRequest
	if err := c.ShouldBindJSON(&req); err != nil || strings.TrimSpace(req.Body) == "" || !policy.TextAllowed(req.Body) {
		c.JSON(http.StatusBadRequest, gin.H{"error": "наблюдение пустое или запрещено"})
		return
	}
	if err := s.store.AddObservation(c.Request.Context(), shardID, ticketID, stepID, strings.TrimSpace(req.Body)); err != nil {
		writeStoreErr(c, err)
		return
	}
	c.JSON(http.StatusCreated, gin.H{"status": "noted"})
}

func (s *Server) completeStep(c *gin.Context) {
	ticketID, shardID, ok := s.locate(c)
	if !ok {
		return
	}
	stepID, err := uuid.Parse(c.Param("step"))
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "step должен быть uuid"})
		return
	}
	var req completeRequest
	_ = c.ShouldBindJSON(&req)
	seen := "false"
	if req.SeenOnScreen {
		seen = "true"
	}
	if err := s.store.CompleteStep(c.Request.Context(), shardID, ticketID, stepID, seen, strings.TrimSpace(req.Body)); err != nil {
		writeStoreErr(c, err)
		return
	}
	c.JSON(http.StatusOK, gin.H{"status": "done"})
}

func (s *Server) reviseStep(c *gin.Context) {
	ticketID, shardID, ok := s.locate(c)
	if !ok {
		return
	}
	stepID, err := uuid.Parse(c.Param("step"))
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "step должен быть uuid"})
		return
	}
	var req observationRequest
	if err := c.ShouldBindJSON(&req); err != nil || !policy.TextAllowed(req.Body) {
		c.JSON(http.StatusForbidden, gin.H{"error": "новый текст шага запрещён"})
		return
	}
	if err := s.store.ReviseStep(c.Request.Context(), shardID, ticketID, stepID, strings.TrimSpace(req.Body)); err != nil {
		writeStoreErr(c, err)
		return
	}
	c.JSON(http.StatusOK, gin.H{"status": "revised"})
}

func (s *Server) postProblem(c *gin.Context) {
	ticketID, shardID, ok := s.locate(c)
	if !ok {
		return
	}
	var req problemRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "некорректное тело"})
		return
	}
	parent, err := uuid.Parse(req.ParentStepID)
	if err != nil || strings.TrimSpace(req.Description) == "" || len(req.Steps) == 0 || len(req.Steps) > 20 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "нужны родительский шаг, описание и шаги"})
		return
	}
	for _, step := range req.Steps {
		if !policy.TextAllowed(step) {
			c.JSON(http.StatusForbidden, gin.H{"error": "шаг запрещён правилами"})
			return
		}
	}
	problemID, err := s.store.AddProblem(c.Request.Context(), shardID, ticketID, parent, strings.TrimSpace(req.Description), req.Steps)
	if err != nil {
		writeStoreErr(c, err)
		return
	}
	c.JSON(http.StatusCreated, gin.H{"problem_id": problemID.String()})
}

func (s *Server) postResolution(c *gin.Context) {
	ticketID, shardID, ok := s.locate(c)
	if !ok {
		return
	}
	var req resolutionRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "некорректное тело"})
		return
	}
	role := principal(c).Role
	if strings.TrimSpace(req.Comment) != "" {
		_ = s.store.AddMessage(c.Request.Context(), shardID, ticketID, role, strings.TrimSpace(req.Comment))
	}
	status, err := s.store.Confirm(c.Request.Context(), shardID, ticketID, role, req.Accept)
	if err != nil {
		writeStoreErr(c, err)
		return
	}
	if status == "resolved" {
		_ = s.cache.RevokeTicketMedia(c.Request.Context(), ticketID.String())
	}
	c.JSON(http.StatusOK, gin.H{"status": status})
}

func (s *Server) getSession(c *gin.Context) {
	ticketID, shardID, ok := s.locate(c)
	if !ok {
		return
	}
	ctx := c.Request.Context()
	ticket, err := s.store.Get(ctx, shardID, ticketID)
	if err != nil {
		writeStoreErr(c, err)
		return
	}
	messages, err := s.store.Messages(ctx, shardID, ticketID)
	if err != nil {
		writeStoreErr(c, err)
		return
	}
	steps, err := s.store.Steps(ctx, shardID, ticketID)
	if err != nil {
		writeStoreErr(c, err)
		return
	}
	inputs, err := s.store.PendingInputs(ctx, shardID, ticketID)
	if err != nil {
		writeStoreErr(c, err)
		return
	}
	confirmations, err := s.store.Confirmations(ctx, shardID, ticketID)
	if err != nil {
		writeStoreErr(c, err)
		return
	}
	grant, err := s.store.ActiveGrant(ctx, shardID, ticketID)
	grantJSON := gin.H{}
	if err != nil && !errors.Is(err, store.ErrNotFound) {
		writeStoreErr(c, err)
		return
	}
	if err == nil {
		grantJSON = gin.H{"grant_id": grant.ID.String(), "status": grant.Status, "scope": grant.Scope, "expires_at": grant.ExpiresAt}
	}
	c.JSON(http.StatusOK, gin.H{
		"ticket":        ticketJSON(ticket),
		"messages":      messages,
		"steps":         steps,
		"inputs":        inputs,
		"confirmations": confirmations,
		"control":       grantJSON,
	})
}
