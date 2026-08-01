from __future__ import annotations


class Strings:
    matrix_welcome: str
    link_success_matrix: str
    link_success_max: str
    unlink_success_matrix: str
    unlink_success_max: str
    unlink_no_bridge_matrix: str
    unlink_no_bridge_max: str
    unlink_failed_matrix: str
    unlink_failed_max: str
    encrypted_room: str
    encrypted_room_link_denied: str
    already_linked_matrix: str
    already_linked_max: str
    code_not_found: str
    code_expired: str
    max_chat_already_linked: str
    rate_limit_link: str
    rate_limit_code: str
    media_relay_failed: str
    media_relay_failed_to_max: str
    attachment_label: str
    not_authorized: str

    def max_welcome(self, matrix_user_id: str, link_code: str) -> str: ...


class RussianStrings(Strings):
    def __init__(self, matrix_user_id: str) -> None:
        self.matrix_welcome = (
            "Здравствуйте! Чтобы связать этот чат с чатом Max:\n"
            "1. Добавьте этого бота в группу Max.\n"
            "2. Сделайте бота администратором (или дайте права на чтение сообщений/медиа).\n"
            "3. Вы получите команду вида /max link [КОД] — введите её здесь."
        )
        self.link_success_matrix = (
            "Чат Max успешно связан с этой комнатой. Сообщения будут пересылаться."
        )
        self.link_success_max = "Комната Matrix успешно связана. Сообщения будут пересылаться."
        self.unlink_success_matrix = "Связка с Max разорвана."
        self.unlink_success_max = "Связка с Matrix разорвана."
        self.unlink_no_bridge_matrix = "Для этой комнаты нет активной связки."
        self.unlink_no_bridge_max = "Для этого чата нет активной связки."
        self.unlink_failed_matrix = "Не удалось разорвать связку. Повторите попытку."
        self.unlink_failed_max = "Не удалось разорвать связку. Повторите попытку."
        self.encrypted_room = (
            "Эта комната использует шифрование (E2EE). Бридж работает только "
            "в незашифрованных комнатах. Создайте новую комнату без шифрования."
        )
        self.encrypted_room_link_denied = (
            "В этой комнате включено шифрование. Бридж поддерживает только незашифрованные комнаты."
        )
        self.already_linked_matrix = "Эта комната Matrix уже связана с чатом Max."
        self.already_linked_max = (
            "Этот чат уже связан с комнатой Matrix. "
            "Сначала выполните /max unlink, затем запрашивайте новый код."
        )
        self.code_not_found = "Код не найден или уже использован. Запросите новый код в Max."
        self.code_expired = "Срок действия кода истёк. Запросите новый код в Max."
        self.max_chat_already_linked = (
            "Этот чат Max уже связан с другой комнатой Matrix. "
            "Сначала удалите старую связь или используйте другой чат."
        )
        self.rate_limit_link = "Слишком много попыток связки. Подождите несколько минут."
        self.rate_limit_code = "Слишком частые запросы кодов связки. Попробуйте позже."
        self.media_relay_failed = (
            "Не удалось переслать вложение в Matrix (размер, формат или сеть)."
        )
        self.media_relay_failed_to_max = (
            "Не удалось переслать вложение в Max (размер, формат или сеть)."
        )
        self.attachment_label = "вложение"
        self.not_authorized = (
            "Нет прав: для этой команды требуются права модератора или администратора."
        )

    def max_welcome(self, matrix_user_id: str, link_code: str) -> str:
        return (
            f"Здравствуйте! Чтобы подключить чат-мост, добавьте бота {matrix_user_id} "
            f"в комнату Matrix и в том же чате введите команду:\n\n"
            f"/max link {link_code}"
        )


def make_strings(matrix_user_id: str) -> Strings:
    """Все сообщения бота - только на русском языке."""
    return RussianStrings(matrix_user_id)
