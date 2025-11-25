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

See `examples/group_shifts.json` for a complete sample aligned with the current production data that includes both buildings.

## Bell schedule JSON format

Bell data is stored with a building layer so the same shift can have different times per campus:

```json
{
  "понедельник": {
    "1": {
      "1_shift": {"1": "09:00–09:45", "2": "09:55–10:40"}
    },
    "2": {
      "1_shift": {"1": "10:10–10:55", "2": "11:05–11:50"}
    },
    "default": {
      "1_shift": {"1": "08:30–09:15", "2": "09:25–10:10"}
    }
  }
}
```

* The path is `bell_schedule[day][building_key][shift_key][lesson_number]`.
* `building_key` is a stringified building number (e.g., `"1"`) or `"default"` for campus-agnostic times.
* When a building-specific block is missing, the backend falls back to the `default` block for that day.

A full, building-aware example is available in `examples/bell_schedule.json`. Building `"1"` mirrors the original single-campus timings, while building `"2"` shows how slight offsets can coexist in the same file.
