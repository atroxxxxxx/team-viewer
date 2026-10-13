package httpapi

import (
	"net/http"

	"ticket-service/internal/auth"
	"ticket-service/internal/token"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
)

const principalKey = "principal"

func (s *Server) requireAuth(c *gin.Context) {
	user, pass, ok := c.Request.BasicAuth()
	principal, allowed := s.creds.Authenticate(user, pass, ok)
	if !allowed {
		c.Header("WWW-Authenticate", `Basic realm="tickets"`)
		c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "нужна аутентификация"})
		return
	}
	c.Set(principalKey, principal)
}

func (s *Server) only(role string) gin.HandlerFunc {
	return func(c *gin.Context) {
		if principal(c).Role != role {
			c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"error": "недостаточно прав"})
		}
	}
}

func (s *Server) reader() gin.HandlerFunc {
	return func(c *gin.Context) {
		p := principal(c)
		if p.Role == auth.RoleOperator {
			return
		}
		s.requirePublisher(c)
	}
}

func (s *Server) clientToken() gin.HandlerFunc {
	return func(c *gin.Context) {
		if principal(c).Role != auth.RoleClient {
			c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"error": "это действие клиента"})
			return
		}
		s.requirePublisher(c)
	}
}

func (s *Server) assignee() gin.HandlerFunc {
	return func(c *gin.Context) {
		p := principal(c)
		if p.Role != auth.RoleOperator {
			c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"error": "это действие оператора"})
			return
		}
		ticketID, shardID, ok := s.locate(c)
		if !ok {
			return
		}
		item, err := s.store.Get(c.Request.Context(), shardID, ticketID)
		if err != nil {
			writeStoreErr(c, err)
			c.Abort()
			return
		}
		if item.Assignee == nil || *item.Assignee != p.ID {
			c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"error": "заявка закреплена за другим оператором"})
		}
	}
}

func (s *Server) participant() gin.HandlerFunc {
	return func(c *gin.Context) {
		p := principal(c)
		if p.Role == auth.RoleClient {
			s.requirePublisher(c)
			return
		}
		s.assignee()(c)
	}
}

func (s *Server) requirePublisher(c *gin.Context) {
	ticketID, shardID, ok := s.locate(c)
	if !ok {
		return
	}
	raw := c.GetHeader("X-Ticket-Token")
	matches, err := s.store.TokenMatches(c.Request.Context(), shardID, ticketID, token.Hash(raw))
	if err != nil {
		writeStoreErr(c, err)
		c.Abort()
		return
	}
	if !matches {
		c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"error": "токен заявки не подходит"})
	}
}

func principal(c *gin.Context) auth.Principal {
	value, _ := c.Get(principalKey)
	item, _ := value.(auth.Principal)
	return item
}

func principalID(c *gin.Context) uuid.UUID {
	return principal(c).ID
}
