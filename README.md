# Schedule Backend

FastAPI backend for uploading and serving schedule data. The `/upload` endpoint accepts a DOCX file and an optional `group_shifts.json` that describes shift metadata for each group.

## `group_shifts.json` format

Provide a JSON object where each key is a group name and the value describes its shift:

```json
{
  "А-11": { "shift": 1, "room": "101", "building": 1 },
  "Б-22": { "shift": 2, "room": "202", "building": 2 }
}
```

* `shift` — number of the shift (1 or 2).
* `room` — default classroom for the group.
* `building` — campus/building number (for example, 1 or 2). If omitted, it is stored as `null`.

The `group_shifts.json` file is optional, but including `building` ensures the field is available in API responses under `shift_info`.
