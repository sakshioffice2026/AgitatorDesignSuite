
using Agitator.Business.Contracts;
using Agitator.Core.Entities;
using Agitator.Models.ViewModel;
using Agitator.Repositories.Contracts;
using Agitator.Utilities.Helpers;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace AgitatorDesignSuite.Web.Controllers
{
    
    public class ProjectsController : BaseController
    {
        private readonly IUnitOfWork _uow;
        private readonly IAgitatorCalculationService _calculationService;
        private readonly ICadJobService _cadJobService;
        private readonly IReportService _reportService;
        private readonly ILogger<ProjectsController> _logger;

        public ProjectsController(
            IUnitOfWork uow,
            IAgitatorCalculationService calculationService,
            ICadJobService cadJobService,
            IReportService reportService,
            ILogger<ProjectsController> logger)
        {
            _uow = uow;
            _calculationService = calculationService;
            _cadJobService = cadJobService;
            _reportService = reportService;
            _logger = logger;
        }

        // GET: /Projects
        public async Task<IActionResult> Index()
        {
            try
            {
                var tenantId = await SetTenantNameAsync();
                SetBreadcrumb(
                    new BreadcrumbItem { Text = "Dashboard", Url = Url.Action("Index", "Home") },
                    new BreadcrumbItem { Text = "Projects", IsActive = true });

                var projects = await _uow.projectRepository.GetProjectIndexDataAsync(tenantId);
                return View(projects);
            }
            catch (Exception ex)
            {
                await _uow.exceptionHandlerRepository.SaveException(nameof(ProjectsController), nameof(Index), ex);
                TempData["Error"] = "Unable to load projects.";
                return View(new List<ProjectListItemViewModel>());
            }
        }

        // GET: /Projects/Detail/5
        public async Task<IActionResult> Detail(int id)
        {
            try
            {
                var project = await _uow.projectRepository.GetProjectDetailAsync(id);
                if (project is null)
                {
                    TempData["Error"] = "Project not found.";
                    return RedirectToAction(nameof(Index));
                }

                SetBreadcrumb(
                    new BreadcrumbItem { Text = "Dashboard", Url = Url.Action("Index", "Home") },
                    new BreadcrumbItem { Text = "Projects", Url = Url.Action("Index") },
                    new BreadcrumbItem { Text = project.Name, IsActive = true });

                return View(MapToDetailViewModel(project));
            }
            catch (Exception ex)
            {
                await _uow.exceptionHandlerRepository.SaveException(nameof(ProjectsController), nameof(Detail), ex);
                TempData["Error"] = "Unable to load project.";
                return RedirectToAction(nameof(Index));
            }
        }

        // GET: /Projects/Create
        [HttpGet]
        public IActionResult Create()
        {
            SetBreadcrumb(
                new BreadcrumbItem { Text = "Dashboard", Url = Url.Action("Index", "Home") },
                new BreadcrumbItem { Text = "Projects", Url = Url.Action("Index") },
                new BreadcrumbItem { Text = "New Project", IsActive = true });

            return View(new ProjectFormViewModel());
        }

        // POST: /Projects/Create
        // Creates the project + child records and immediately runs the
        // calculation engine so the user lands on results without a
        // separate step.
        [HttpPost]
        [ValidateAntiForgeryToken]
        public async Task<IActionResult> Create(ProjectFormViewModel vm)
        {
            if (!ModelState.IsValid)
                return View(vm);

            try
            {
                var tenantId = await SetTenantNameAsync();
                var userId = await GetCurrentUserAsync();

                var (success, error, projectId) = await _uow.projectRepository.CreateProjectAsync(vm, tenantId, userId);
                if (!success)
                {
                    TempData["Error"] = error ?? "Unable to create project.";
                    return View(vm);
                }

                var project = await _uow.projectRepository.GetProjectDetailAsync(projectId);
                var result = _calculationService.Calculate(project!.Vessel!, project.Impeller!, project.FluidProperties!);
                result.ProjectId = projectId;
                await _uow.calculationResultRepository.CreateAsync(result);

                _logger.LogInformation("Created project {ProjectId} for tenant {TenantId}", projectId, tenantId);
                TempData["Success"] = "Project created and calculated successfully.";
                return RedirectToAction(nameof(Results), new { id = projectId });
            }
            catch (Exception ex)
            {
                await _uow.exceptionHandlerRepository.SaveException(nameof(ProjectsController), nameof(Create), ex);
                TempData["Error"] = "Something went wrong while creating the project.";
                return View(vm);
            }
        }

        // GET: /Projects/Edit/5
        [HttpGet]
        public async Task<IActionResult> Edit(int id)
        {
            try
            {
                var tenantId = await SetTenantNameAsync();
                if (!await _uow.projectRepository.CanEditProjectAsync(id, tenantId))
                {
                    TempData["Error"] = "You cannot edit this project.";
                    return RedirectToAction(nameof(Index));
                }

                var project = await _uow.projectRepository.GetProjectDetailAsync(id);
                if (project is null)
                {
                    TempData["Error"] = "Project not found.";
                    return RedirectToAction(nameof(Index));
                }

                SetBreadcrumb(
                    new BreadcrumbItem { Text = "Dashboard", Url = Url.Action("Index", "Home") },
                    new BreadcrumbItem { Text = "Projects", Url = Url.Action("Index") },
                    new BreadcrumbItem { Text = $"Edit {project.Name}", IsActive = true });

                return View(MapToFormViewModel(project));
            }
            catch (Exception ex)
            {
                await _uow.exceptionHandlerRepository.SaveException(nameof(ProjectsController), nameof(Edit), ex);
                TempData["Error"] = "Unable to load project for editing.";
                return RedirectToAction(nameof(Index));
            }
        }

        // POST: /Projects/Edit/5
        [HttpPost]
        [ValidateAntiForgeryToken]
        public async Task<IActionResult> Edit(int id, ProjectFormViewModel vm)
        {
            if (!ModelState.IsValid)
                return View(vm);

            try
            {
                var userId = await GetCurrentUserAsync();
                var (success, error) = await _uow.projectRepository.UpdateProjectAsync(id, vm, userId);

                if (!success)
                {
                    TempData["Error"] = error ?? "Unable to update project.";
                    return View(vm);
                }

                // Recalculate since geometry/fluid inputs may have changed.
                var project = await _uow.projectRepository.GetProjectDetailAsync(id);
                var result = _calculationService.Calculate(project!.Vessel!, project.Impeller!, project.FluidProperties!);

                TempData["Success"] = "Project updated successfully.";
                return RedirectToAction(nameof(Detail), new { id });
            }
            catch (Exception ex)
            {
                await _uow.exceptionHandlerRepository.SaveException(nameof(ProjectsController), nameof(Edit), ex);
                TempData["Error"] = "Something went wrong while updating the project.";
                return View(vm);
            }
        }

        // POST: /Projects/ChangeStatus/5
        // Toggles CAD job status back to NotRequested (e.g. to allow re-queueing after a Failed job).
        [HttpPost]
        [ValidateAntiForgeryToken]
        public async Task<IActionResult> ChangeStatus(int id)
        {
            try
            {
                var (success, error) = await _uow.calculationResultRepository.ChangeCadStatusAsync(id, CadJobStatus.NotRequested);
                TempData[success ? "Success" : "Error"] = success ? "Status reset." : error;
                return RedirectToAction(nameof(Results), new { id });
            }
            catch (Exception ex)
            {
                await _uow.exceptionHandlerRepository.SaveException(nameof(ProjectsController), nameof(ChangeStatus), ex);
                TempData["Error"] = "Unable to change status.";
                return RedirectToAction(nameof(Results), new { id });
            }
        }

        // POST: /Projects/Delete/5
        [HttpPost]
        [ValidateAntiForgeryToken]
        public async Task<IActionResult> Delete(int id)
        {
            try
            {
                var (success, error) = await _uow.projectRepository.DeleteProjectAsync(id);
                TempData[success ? "Success" : "Error"] = success ? "Project deleted." : error;
                return RedirectToAction(nameof(Index));
            }
            catch (Exception ex)
            {
                await _uow.exceptionHandlerRepository.SaveException(nameof(ProjectsController), nameof(Delete), ex);
                TempData["Error"] = "Unable to delete project.";
                return RedirectToAction(nameof(Index));
            }
        }

        // GET: /Projects/Results/5
        public async Task<IActionResult> Results(int id)
        {
            try
            {
                var project = await _uow.projectRepository.GetProjectDetailAsync(id);
                if (project is null)
                {
                    TempData["Error"] = "Project not found.";
                    return RedirectToAction(nameof(Index));
                }

                SetBreadcrumb(
                    new BreadcrumbItem { Text = "Dashboard", Url = Url.Action("Index", "Home") },
                    new BreadcrumbItem { Text = "Projects", Url = Url.Action("Index") },
                    new BreadcrumbItem { Text = $"{project.Name} — Results", IsActive = true });

                return View(MapToDetailViewModel(project));
            }
            catch (Exception ex)
            {
                await _uow.exceptionHandlerRepository.SaveException(nameof(ProjectsController), nameof(Results), ex);
                TempData["Error"] = "Unable to load results.";
                return RedirectToAction(nameof(Index));
            }
        }

        // POST: /Projects/RequestCadModel/5
        [HttpPost]
        [ValidateAntiForgeryToken]
        public async Task<IActionResult> RequestCadModel(int id)
        {
            try
            {
                var (success, error) = await _cadJobService.EnqueueAsync(id);
                TempData[success ? "Success" : "Error"] = success
                    ? "3D model generation has been queued. Refresh in a few moments."
                    : error;
                return RedirectToAction(nameof(Results), new { id });
            }
            catch (Exception ex)
            {
                await _uow.exceptionHandlerRepository.SaveException(nameof(ProjectsController), nameof(RequestCadModel), ex);
                TempData["Error"] = "Unable to queue 3D model generation.";
                return RedirectToAction(nameof(Results), new { id });
            }
        }

        // GET: /Projects/DownloadReport/5
        public async Task<IActionResult> DownloadReport(int id)
        {
            try
            {
                var project = await _uow.projectRepository.GetProjectDetailAsync(id);
                if (project is null)
                {
                    TempData["Error"] = "Project not found.";
                    return RedirectToAction(nameof(Index));
                }

                var pdfPath = _reportService.GenerateDatasheet(project);
                await _uow.calculationResultRepository.SetReportPathAsync(id, pdfPath);

                var bytes = await System.IO.File.ReadAllBytesAsync(pdfPath);
                return File(bytes, "application/pdf", $"{project.Name}-datasheet.pdf");
            }
            catch (Exception ex)
            {
                await _uow.exceptionHandlerRepository.SaveException(nameof(ProjectsController), nameof(DownloadReport), ex);
                TempData["Error"] = "Unable to generate report.";
                return RedirectToAction(nameof(Results), new { id });
            }
        }

        private static ProjectDetailViewModel MapToDetailViewModel(Project project)
        {
            return new ProjectDetailViewModel
            {
                Id = project.Id,
                Name = project.Name,
                Description = project.Description,
                CreatedAt = project.CreatedAt,
                UpdatedAt = project.UpdatedAt,
                Vessel = project.Vessel,
                Impeller = project.Impeller,
                FluidProperties = project.FluidProperties,
                CalculationResult = project.CalculationResult
            };
        }

        private static ProjectFormViewModel MapToFormViewModel(Project project)
        {
            return new ProjectFormViewModel
            {
                Id = project.Id,
                Name = project.Name,
                Description = project.Description,
                VesselDiameterM = project.Vessel?.DiameterM ?? 2.0,
                LiquidHeightM = project.Vessel?.LiquidHeightM ?? 2.0,
                HasBaffles = project.Vessel?.HasBaffles ?? true,
                NumberOfBaffles = project.Vessel?.NumberOfBaffles ?? 4,
                ImpellerType = project.Impeller?.Type ?? ImpellerType.RushtonTurbine,
                ImpellerDiameterM = project.Impeller?.DiameterM ?? 0.7,
                RotationalSpeedRpm = project.Impeller?.RotationalSpeedRpm ?? 90,
                NumberOfImpellers = project.Impeller?.NumberOfImpellers ?? 1,
                DensityKgM3 = project.FluidProperties?.DensityKgM3 ?? 1000,
                ViscosityPaS = project.FluidProperties?.ViscosityPaS ?? 0.001
            };
        }
    }
}