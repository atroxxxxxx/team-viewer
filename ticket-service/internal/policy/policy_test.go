package policy

import "testing"

func TestIntentAllowed(t *testing.T) {
	if IntentAllowed("delete_file") || IntentAllowed("") {
		t.Fatal("запрещённое или пустое намерение прошло")
	}
	if !IntentAllowed("adjust_app_setting") {
		t.Fatal("обычное намерение отклонено")
	}
}

func TestTextAllowed(t *testing.T) {
	if TextAllowed("выполните rm -rf ~") {
		t.Fatal("шаг с удалением прошёл")
	}
	if !TextAllowed("откройте меню Вид и включите линейку") {
		t.Fatal("обычный шаг отклонён")
	}
}
