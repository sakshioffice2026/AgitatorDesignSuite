using System.Text.Json.Serialization;
using Agitator.Business;
using Agitator.Business.Contracts;
using Agitator.Core;
using Agitator.Models.DTOs;
using Agitator.Repositories;
using Agitator.Repositories.Contracts;
using Microsoft.EntityFrameworkCore;

var builder = WebApplication.CreateBuilder(args);

// --- MVC + camelCase JSON ---
builder.Services.AddControllersWithViews()
    .AddJsonOptions(options =>
    {
        options.JsonSerializerOptions.PropertyNamingPolicy = System.Text.Json.JsonNamingPolicy.CamelCase;
        options.JsonSerializerOptions.Converters.Add(new JsonStringEnumConverter());
    });

// --- MySQL via Pomelo EF Core provider ---
var connectionString = builder.Configuration.GetConnectionString("DefaultConnection")
    ?? throw new InvalidOperationException("Connection string 'DefaultConnection' not found in appsettings.json");

builder.Services.AddDbContext<ApplicationDbContext>(options =>
    options.UseMySql(connectionString, ServerVersion.AutoDetect(connectionString)));

// --- Cookie authentication ---
builder.Services.AddAuthentication(Microsoft.AspNetCore.Authentication.Cookies.CookieAuthenticationDefaults.AuthenticationScheme)
    .AddCookie(options =>
    {
        options.LoginPath = "/Account/Login";
        options.AccessDeniedPath = "/Account/AccessDenied";
        options.ExpireTimeSpan = TimeSpan.FromHours(8);
        options.SlidingExpiration = true;
    });

// --- Options binding ---
builder.Services.Configure<CadIntegrationOptions>(builder.Configuration.GetSection("CadIntegration"));
builder.Services.Configure<ReportGenerationOptions>(builder.Configuration.GetSection("ReportGeneration"));

// --- Repositories / UnitOfWork ---
builder.Services.AddScoped<IUnitOfWork, UnitOfWorks>();
builder.Services.AddScoped<IProjectRepository, Agitator.Repositories.Repositories.ProjectRepository>();
builder.Services.AddScoped<ICalculationResultRepository, Agitator.Repositories.Repositories.CalculationResultRepository>();
builder.Services.AddScoped<IExceptionHandlerRepository, Agitator.Repositories.Repositories.ExceptionHandlerRepository>();

// --- Business services ---
builder.Services.AddScoped<IAgitatorCalculationService, AgitatorCalculationService>();
builder.Services.AddScoped<ICadJobService, CadJobService>();
builder.Services.AddScoped<IReportService, ReportService>();

// --- Background worker that polls for CAD jobs and invokes FreeCAD ---
builder.Services.AddHostedService<CadJobBackgroundService>();

var app = builder.Build();

// --- Ensure DB created + seed on startup (dev convenience;
//     for production, prefer running migrations explicitly as a deploy step) ---
using (var scope = app.Services.CreateScope())
{
    var db = scope.ServiceProvider.GetRequiredService<ApplicationDbContext>();
    await SeedData.InitializeAsync(db);
}

if (!app.Environment.IsDevelopment())
{
    app.UseExceptionHandler("/Home/Error");
    app.UseHsts();
}

app.UseHttpsRedirection();
app.UseStaticFiles();
app.UseRouting();
app.UseAuthentication();
app.UseAuthorization();

app.MapControllerRoute(
    name: "default",
    pattern: "{controller=Home}/{action=Index}/{id?}");

app.Run();