package policy

import "strings"

var forbiddenIntents = map[string]struct{}{
	"delete_file":        {},
	"empty_trash":        {},
	"format_disk":        {},
	"wipe_account":       {},
	"disable_protection": {},
	"open_inbound_port":  {},
	"create_persistence": {},
	"exfiltrate":         {},
	"read_secrets":       {},
	"run_shell":          {},
}

var forbiddenText = []string{
	"rm -", "rm\t-", "shred ", "mkfs", "remove-item", "del /",
	"format c:", "diskpart", "dd if=", "useradd", "net user",
	"schtasks", "iptables", "ufw ", "passwd", "authorized_keys",
}

func IntentAllowed(intent string) bool {
	_, bad := forbiddenIntents[strings.TrimSpace(intent)]
	return strings.TrimSpace(intent) != "" && !bad
}

func ScopeAllows(scope, kind string) bool {
	for _, part := range strings.Split(scope, ",") {
		if strings.TrimSpace(part) == kind {
			return true
		}
	}
	return false
}

func NormalizeScope(scope string) (string, bool) {
	allowed := map[string]struct{}{"pointer": {}, "key": {}, "text": {}}
	var out []string
	seen := map[string]struct{}{}
	for _, part := range strings.Split(scope, ",") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		if _, ok := allowed[part]; !ok {
			return "", false
		}
		if _, ok := seen[part]; ok {
			continue
		}
		seen[part] = struct{}{}
		out = append(out, part)
	}
	if len(out) == 0 {
		return "", false
	}
	return strings.Join(out, ","), true
}

func TextAllowed(text string) bool {
	folded := strings.ToLower(text)
	for _, item := range forbiddenText {
		if strings.Contains(folded, item) {
			return false
		}
	}
	return true
}
