# BGP Audit - Project Guidelines

## Stack
- **Backend:** FastAPI
- **Templates:** Jinja2
- **Frontend:** Alpine.js / Tailwind CSS

## Architecture
- Running in Docker behind Nginx at the `/audit` subpath

## Constraints

### Root Path Configuration
Keep `app = FastAPI(root_path="/audit")` and ensure all internal URLs are relative or respect the root path.

### Design Elements
Do not change any existing design elements.
