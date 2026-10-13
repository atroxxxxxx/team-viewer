package relayclient

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/google/uuid"
)

type Client struct {
	base  string
	token string
	http  *http.Client
}

func New(base, token string) *Client {
	return &Client{
		base:  base,
		token: token,
		http:  &http.Client{Timeout: 5 * time.Second},
	}
}

type openRequest struct {
	ClientID string `json:"client_id"`
	TicketID string `json:"ticket_id"`
	Camera   bool   `json:"camera"`
}

type openResponse struct {
	TunnelID string `json:"tunnel_id"`
}

func (c *Client) Open(ctx context.Context, clientID, ticketID uuid.UUID, camera bool) (uuid.UUID, error) {
	body, _ := json.Marshal(openRequest{ClientID: clientID.String(), TicketID: ticketID.String(), Camera: camera})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.base+"/api/v1/tunnels", bytes.NewReader(body))
	if err != nil {
		return uuid.UUID{}, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+c.token)
	resp, err := c.http.Do(req)
	if err != nil {
		return uuid.UUID{}, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode != http.StatusCreated {
		return uuid.UUID{}, fmt.Errorf("ретранслятор ответил %d", resp.StatusCode)
	}
	var out openResponse
	if err := json.Unmarshal(raw, &out); err != nil {
		return uuid.UUID{}, err
	}
	return uuid.Parse(out.TunnelID)
}

func (c *Client) Close(ctx context.Context, ticketID uuid.UUID) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodDelete, c.base+"/api/v1/tunnels/"+ticketID.String(), nil)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+c.token)
	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, resp.Body)
	if resp.StatusCode != http.StatusNoContent && resp.StatusCode != http.StatusNotFound {
		return fmt.Errorf("ретранслятор ответил %d", resp.StatusCode)
	}
	return nil
}
