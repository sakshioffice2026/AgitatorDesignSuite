# Agitator Design Suite — Starter Scaffold

A working starting point for a web-based industrial agitator design tool:
**ASP.NET Core MVC + MySQL** for UI/backend/database, a **C# calculation
engine** for process/mechanical sizing, **FreeCAD** (headless) for parametric
3D model generation, and **QuestPDF** for report generation.

This is a functional foundation with one complete end-to-end flow — it is
**not** a finished production SaaS. Sections below flag what's stubbed vs.
production-ready.

## What's implemented end-to-end

1. Create a project (vessel geometry, impeller, fluid properties) via a web form
2. Run the calculation engine (Reynolds number, power number, power draw,
   torque, tip speed, recommended shaft diameter, recommended motor rating)
3. Persist everything to MySQL via EF Core
4. View results in the browser
5. Download a PDF engineering datasheet
6. Queue a FreeCAD 3D model generation job (background service architecture
   is complete; the FreeCAD macro is a geometry skeleton — see below)

## What's stubbed / needs work before production

- **Authentication & multi-tenancy**: `TenantId` field exists on `Project`
  but there's no login system wired up yet. Add ASP.NET Core Identity (or
  your SSO provider) and populate `TenantId` from the authenticated user's
  claims, plus row-level scoping on every query.
- **FreeCAD geometry**: `CadScripts/generate_agitator_model.py` builds
  placeholder cylinders for impellers rather than true blade geometry per
  impeller type. Extend `build_impeller_disc()` with real geometry per
  `ImpellerType`.
- **3D viewer**: The Three.js `<script>` block in `Results.cshtml` is a stub
  — wire up `THREE.GLTFLoader` once you have a real generated mesh to test
  against.
- **Calculation coefficients**: `AgitatorCalculationService.ImpellerCoefficients`
  uses representative textbook Kp/Ct values. Replace with your own validated
  correlations before using this for real equipment sizing — see the
  liability notes below.
- **Liquid-liquid / gas dispersion / solids suspension** calculations are not
  included — only single-phase Newtonian mixing power is covered in v1.

## Prerequisites

- .NET 8 SDK
- MySQL Server 8.x
- FreeCAD 0.21+ installed on the machine running the CAD background worker
  (`freecadcmd` must be on the configured path)

## Setup

### 1. Database

Run the SQL script as a MySQL admin to create the database and app user:

```bash
mysql -u root -p < database/00_create_database_and_user.sql
```

Update the password in that script (and in `appsettings.json`) before running
in anything beyond local dev.

### 2. Connection string & paths

Edit `src/AgitatorDesignSuite.Web/appsettings.json`:

- `ConnectionStrings:DefaultConnection` — your MySQL credentials
- `CadIntegration:FreeCadCmdPath` — path to `freecadcmd` on your server
- `CadIntegration:OutputDirectory` — writable folder for generated STEP/mesh files
- `ReportGeneration:OutputDirectory` — writable folder for generated PDFs

For anything beyond local dev, move secrets out of `appsettings.json` into
environment variables or `dotnet user-secrets`.

### 3. Create the initial EF Core migration

No migration exists yet in this scaffold — generate it against your local
MySQL instance:

```bash
cd src/AgitatorDesignSuite.Web
dotnet tool install --global dotnet-ef   # if not already installed
dotnet ef migrations add InitialCreate
```

The app calls `db.Database.Migrate()` on startup (see `Program.cs`), so the
schema will be created automatically the first time you run it after that.

### 4. Run

```bash
dotnet restore
dotnet run
```

Navigate to `https://localhost:5001` (or the port shown in the console).

## Project layout

```
AgitatorDesignSuite.sln
src/AgitatorDesignSuite.Web/
  Controllers/        MVC controllers (Home, Projects)
  Models/              EF entities + view models
  Data/                ApplicationDbContext (MySQL via Pomelo)
  Services/            Calculation engine, CAD job queue + worker, PDF reports
  CadScripts/          FreeCAD headless Python macro
  Views/               Razor views
  wwwroot/             CSS/JS
database/
  00_create_database_and_user.sql
```

## Recommended next steps, in priority order

1. Wire up authentication and enforce `TenantId` scoping everywhere
2. Validate/replace the Np/Kp/Ct calculation coefficients against a trusted
   source, and add a "show calculation basis" panel for customer trust
   (discussed as a SaaS trust-building feature)
3. Build out real impeller blade geometry in the FreeCAD macro
4. Move the CAD background worker to its own deployable service (systemd
   unit or Windows Service) rather than in-process with the web app, once
   load grows — the current in-process `BackgroundService` is fine for early
   validation but will compete with web requests for CPU under load
5. Add a licensing/tiering layer (Basic calculations vs. Pro CAD export vs.
   Enterprise) per the earlier SaaS business-model discussion
6. Add a validation report comparing outputs against a known published
   example, for customer-facing accuracy trust
