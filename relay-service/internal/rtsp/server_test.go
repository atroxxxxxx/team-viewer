package rtsp

import (
	"sync/atomic"
	"testing"
	"time"
)

func TestWaitForFindsLateStream(t *testing.T) {
	var ready atomic.Bool
	go func() {
		time.Sleep(30 * time.Millisecond)
		ready.Store(true)
	}()
	value, ok := waitFor(time.Second, 10*time.Millisecond, func() (string, bool) {
		if ready.Load() {
			return "screen", true
		}
		return "", false
	})
	if !ok || value != "screen" {
		t.Fatalf("late stream: %q %v", value, ok)
	}
}

func TestWaitForTimesOut(t *testing.T) {
	_, ok := waitFor(40*time.Millisecond, 10*time.Millisecond, func() (string, bool) {
		return "", false
	})
	if ok {
		t.Fatal("missing stream must time out")
	}
}
