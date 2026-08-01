---
name: http-client
description: "Make HTTP requests using Python httpx. Use for API calls, web scraping, data fetching, and REST API interactions."
internal: true
---

# HTTP Client

Use `python_exec` with registered functions or inline `httpx` for HTTP operations.

## Registered Functions

### GET request
```
python_exec(function="http_get", args=["https://api.example.com/data"])
```

### POST request
```
python_exec(function="http_post", kwargs={"url": "https://api.example.com/data", "json_data": {"key": "value"}})
```

### With custom headers
```
python_exec(code="""
import httpx
resp = httpx.get("https://api.example.com/data", headers={"Authorization": "Bearer TOKEN"}, timeout=30)
print(resp.status_code)
print(resp.text)
""")
```

### POST with form data
```
python_exec(code="""
import httpx
resp = httpx.post("https://api.example.com/form", data={"field": "value"})
print(resp.text)
""")
```

## Tips

- Use `http_get` / `http_post` for simple requests
- Use inline `httpx` code for custom headers, auth, pagination, or streaming
- Default timeout is 30s; increase for slow APIs
- Responses are returned as text; use `json_parse` to parse JSON responses
