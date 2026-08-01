---
name: data-processing
description: "Process data with Python — JSON, CSV, regex, calculations, hashing, encoding. Use for data transformation, parsing, and analysis."
internal: true
---

# Data Processing

Use `python_exec` with registered functions or inline Python for data operations.

## JSON

### Parse JSON
```
python_exec(function="json_parse", args=['{"key": "value"}'])
```

### Dump to JSON
```
python_exec(function="json_dump", args=[{"key": "value"}], kwargs={"indent": 2})
```

### Read/write JSON files
```
python_exec(function="read_json", args=["/path/to/data.json"])
python_exec(function="write_json", kwargs={"path": "/path/to/out.json", "data": {"key": "value"}})
```

## Regex

### Find matches
```
python_exec(function="regex_match", args=[r'\d+', 'abc 123 def 456'])
```

### Replace
```
python_exec(function="regex_replace", args=[r'\s+', '-', 'hello world  foo'])
```

## CSV

```
python_exec(code="""
import csv, io
reader = csv.reader(io.StringIO('name,age\\nAlice,30\\nBob,25'))
for row in reader:
    print(row)
""")
```

## Hashing

```
python_exec(function="md5", args=["hello world"])
python_exec(function="sha256", args=["hello world"])
```

## Encoding

```
python_exec(function="base64_encode", args=["hello world"])
python_exec(function="base64_decode", args=["aGVsbG8gd29ybGQ="])
python_exec(function="url_encode", args=["hello world&foo=bar"])
python_exec(function="url_decode", args=["hello+world%26foo%3Dbar"])
```

## Math & Calculations

```
python_exec(code="import math; result = math.sqrt(144)")
python_exec(code="result = sum(range(1, 101))")
```
