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

## Развёртывание на Railway

1. Отправьте проект в GitHub и создайте в Railway сервис из этого репозитория.
   Корневая папка сервиса должна содержать `Dockerfile` и `railway.json`.
2. Добавьте к сервису **Volume** с путём монтирования **`/data`** до использования API.
   В Docker задано `DATABASE_PATH=/data/auth.db`. Без Volume база будет храниться
   во временной файловой системе и может исчезнуть при следующем развёртывании.
3. Оставьте **Start Command** пустым: команда запуска уже задана в `Dockerfile`.
   Сервер слушает `0.0.0.0` и порт из переменной `PORT`, которую задаёт Railway.
   `--reload` отключён; используется один worker.
4. Разверните сервис и включите **Settings → Networking → Generate Domain**.
   Проверьте `https://<ваш-домен>/health` (ответ `{"status":"ok"}`)
   и `https://<ваш-домен>/docs`.

`railway.json` задаёт Docker-сборку, проверку `/health` при развёртывании,
одну реплику и перезапуск при сбое. Volume и публичный домен нужно создать
в Railway отдельно. Используйте одну реплику с этой SQLite-базой; для нескольких
реплик потребуется переход на общую серверную базу данных.
Создание таблиц выполняется при запуске приложения, когда Volume уже подключён.
Локальная база `data/auth.db` не включается в образ: первое развёртывание создаст
пустую базу. При смене пути `DATABASE_PATH` он должен оставаться внутри Volume.

Настройки окружения:

| Переменная | Локально | Docker / Railway |
| --- | --- | --- |
| `DATABASE_PATH` | `data/auth.db` | `/data/auth.db` (задано в образе) |
| `PORT` | `8000` для команды ниже | Назначается Railway; по умолчанию в Docker `8000` |

Проверка Docker локально (требуется Docker):

```powershell
docker build -t locus-auth .
docker run --rm -p 8000:8000 -e PORT=8000 -v locus-auth-data:/data locus-auth
```

Именованный Volume `locus-auth-data` сохраняет базу после остановки контейнера.
Инструкции Railway: [Dockerfiles](https://docs.railway.com/builds/dockerfiles),
[Volumes](https://docs.railway.com/volumes),
[Config as Code](https://docs.railway.com/config-as-code/reference).

## Методы

| Метод | Путь | Назначение |
| --- | --- | --- |
| GET | `/health` | Проверка доступности |
| POST | `/auth/register` | Регистрация, возвращает id и username (201) |
| POST | `/auth/login` | Вход, возвращает access_token, token_type и expires_in |
| GET | `/auth/me` | Текущий пользователь по Bearer-токену |
| POST | `/auth/logout` | Отзыв текущего токена (204) |
| POST | `/survey` | Сохранить или заменить анкету текущего пользователя (200) |
| GET | `/survey` | Получить сохранённую анкету текущего пользователя (200) |
| POST | `/recommendations?limit=5` | Рекомендации программ по профилю в теле запроса (200) |
| GET | `/recommendations?limit=5` | Рекомендации по сохранённой анкете текущего пользователя (200) |

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

Railway-конфигурация позволяет разместить API с постоянным хранилищем SQLite.
Для публичного использования используйте HTTPS и добавьте ограничение частоты
запросов регистрации и входа; встроенного ограничения в этом API пока нет.

## Анкета

Оба метода `/survey` требуют заголовок `Authorization: Bearer <access_token>`.
`POST /survey` принимает объект `survey` со всеми 15 полями. Имена полей
чувствительны к регистру, включая `SAT`, `IELTS`, `NUET`, `UNT` и `AET`.
Неизвестные и отсутствующие поля возвращают 422. Для вопроса без ответа
передайте `null`. Форматы ответов пока не ограничены: строки, числа, булевы
значения, массивы, объекты и `null` сохраняются как JSON без преобразования.
Диапазоны баллов экзаменов и допустимые варианты ответов пока не проверяются.

Пример тела запроса:

```json
{
  "survey": {
    "grade": 11,
    "entryYear": 2027,
    "interest": ["engineering"],
    "city": "Алматы",
    "mustStay": false,
    "budget": 2000000,
    "funding": ["scholarship"],
    "category": "university",
    "academicStrengths": ["math", "physics"],
    "SAT": 1400,
    "IELTS": 7.5,
    "NUET": null,
    "UNT": 120,
    "AET": null,
    "extracurricularInterests": ["robotics"]
  }
}
```

Отправка из JavaScript (`survey` — объект ответов выше):

```javascript
const response = await fetch(`${apiBaseUrl}/survey`, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${accessToken}`,
  },
  body: JSON.stringify({ survey }),
});
if (!response.ok) throw new Error(`Survey save failed: ${response.status}`);
const saved = await response.json();
```

Ответ `POST /survey` и `GET /survey` имеет ту же форму `{ "survey": { ... } }`.
До первого сохранения `GET /survey` возвращает 404. Без действующей сессии
оба метода возвращают 401. На пользователя хранится одна анкета; повторный
POST полностью заменяет предыдущие ответы, а не дополняет их. Пользователь
определяется по токену, передавать `user_id` нельзя.

Таблица `surveys` создаётся автоматически при запуске, в том числе в существующей
базе с пользователями и сессиями. Данные сохраняются в том же SQLite-файле;
на Railway для сохранения между развёртываниями необходим Volume `/data`.

## Рекомендации программ

Добавлен детерминированный движок подбора образовательных программ: нормализация
профиля → ограничения города/года → проверка альтернативных путей поступления →
сопоставление интересов → оценки компонентов и динамические веса → рекомендации,
объяснения, план подготовки и следующее действие. Оба метода требуют Bearer-токен.
`POST /recommendations` принимает сам объект ответов, без обёртки `survey`;
`GET /recommendations` использует сохранённую анкету. По умолчанию возвращается
до пяти программ, `limit` можно задать от 1 до 50.

`matchScore` от 0 до 1 означает совместимость с профилем, а не вероятность
поступления. Реальные требования вузов не добавлены: без проверенного каталога
ответ содержит пустой список и предупреждение. Демо-программы исключены из
производственного каталога. Существующие пользователи, сессии и анкеты сохраняются.

Архитектура, правила оценки, работа с источниками, импорт каталога, примеры,
embedding-интерфейс и ограничения описаны в [recommendation/README.md](recommendation/README.md).
Новые зависимости не требуются; по умолчанию работает локальное сопоставление
ключевых слов с кэшем, без LLM и скачивания модели.

## Проверка

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```
