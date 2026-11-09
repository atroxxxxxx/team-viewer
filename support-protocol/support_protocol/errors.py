"""Тексты для пользователя отделены от диагностических исключений."""
CONNECTION_MESSAGE = "Не удалось подключиться к сервису заявок. Проверьте соединение и доступность сервера."


class TicketError(RuntimeError):
    pass


class ServiceUnavailable(TicketError):
    def __init__(self):
        super().__init__(CONNECTION_MESSAGE)


def user_message(error):
    if isinstance(error, TicketError):
        return str(error)
    return "Не удалось выполнить действие. Подробности записаны в журнал."
