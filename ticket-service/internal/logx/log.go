package logx

import (
	"encoding/json"
	"os"
	"time"
)

func Info(msg string, fields map[string]any) {
	write("info", msg, fields)
}

func Error(msg string, fields map[string]any) {
	write("error", msg, fields)
}

func write(level, msg string, fields map[string]any) {
	row := map[string]any{
		"ts":      time.Now().UTC().Format(time.RFC3339Nano),
		"level":   level,
		"service": "ticket-service",
		"msg":     msg,
	}
	for k, v := range fields {
		row[k] = v
	}
	enc := json.NewEncoder(os.Stdout)
	_ = enc.Encode(row)
}
