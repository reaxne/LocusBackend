# Locus Auth API

Базовый сервер на Python 3.10+ с FastAPI и SQLite.

## Запуск (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --reload
```

Сервер: http://127.0.0.1:8000. Интерактивная документация: http://127.0.0.1:8000/docs.
SQLite создаётся автоматически в `data/auth.db`. Путь можно изменить переменной окружения `DATABASE_PATH`.

## Методы

| Метод | Путь | Назначение |
| --- | --- | --- |
| GET | `/health` | Проверка доступности |
| POST | `/auth/register` | Регистрация, возвращает id и username (201) |
| POST | `/auth/login` | Вход, возвращает access_token, token_type и expires_in |
| GET | `/auth/me` | Текущий пользователь по Bearer-токену |
| POST | `/auth/logout` | Отзыв текущего токена (204) |

Для регистрации и входа передайте JSON:

```json
{"username": "alice", "password": "my-strong-password"}
```

Логин: 3–50 латинских букв, цифр или `_`, без учёта регистра.
Пароль: 8–128 символов, пробелы и регистр сохраняются.
Повторная регистрация возвращает 409, неверные данные входа или токен — 401,
ошибки формата — 422.

Пример полного сценария в PowerShell:

```powershell
$body = @{ username = "alice"; password = "my-strong-password" } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/auth/register -Method Post -ContentType 'application/json' -Body $body
$session = Invoke-RestMethod http://127.0.0.1:8000/auth/login -Method Post -ContentType 'application/json' -Body $body
$headers = @{ Authorization = "Bearer $($session.access_token)" }
Invoke-RestMethod http://127.0.0.1:8000/auth/me -Headers $headers
Invoke-RestMethod http://127.0.0.1:8000/auth/logout -Method Post -Headers $headers
```

В `/docs` скопируйте `access_token` из ответа `/auth/login` в кнопку **Authorize**.

Пароли хешируются PBKDF2-HMAC-SHA256 (600 000 итераций) с индивидуальной
случайной солью. Случайные токены действуют 24 часа; в базе хранятся только их
SHA-256 хеши. Выход отзывает одну сессию, остальные остаются действительными.
Пользователи и сессии сохраняются при перезапуске.

Это основа для локальной разработки. Для публичного размещения нужны HTTPS
и ограничение частоты запросов регистрации и входа; `--reload` следует отключить.

## Проверка

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```
