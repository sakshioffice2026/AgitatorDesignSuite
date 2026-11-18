using Agitator.Business.Contracts;
using Agitator.Business.Services;
using Agitator.Core.Entities;
using Agitator.Models.DTOs;
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
        private readonly IShaftPredictionService _shaftPredictionService;

        public ProjectsController(
            IUnitOfWork uow,
            IAgitatorCalculationService calculationService,
            ICadJobService cadJobService,
            IReportService reportService,
            ILogger<ProjectsController> logger, IShaftPredictionService shaftPredictionService)
        {
            _uow = uow;
            _calculationService = calculationService;
            _cadJobService = cadJobService;
            _reportService = reportService;
            _logger = logger;
            _shaftPredictionService = shaftPredictionService;
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
        public async Task<IActionResult> Create()
        {
            SetBreadcrumb(
                new BreadcrumbItem { Text = "Dashboard", Url = Url.Action("Index", "Home") },
                new BreadcrumbItem { Text = "Projects", Url = Url.Action("Index") },
                new BreadcrumbItem { Text = "New Project", IsActive = true });

            return await ViewFormAsync(new ProjectFormViewModel());
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
                return await ViewFormAsync(vm);

            if (!await ValidateShaftTemperatureAsync(vm))
                return await ViewFormAsync(vm);

            try
            {
                var tenantId = await SetTenantNameAsync();
                var userId = await GetCurrentUserAsync();

                var (success, error, projectId) = await _uow.projectRepository.CreateProjectAsync(vm, tenantId, userId);
                if (!success)
                {
                    TempData["Error"] = error ?? "Unable to create project.";
                    return await ViewFormAsync(vm);
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
                return await ViewFormAsync(vm);
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

                return await ViewFormAsync(MapToFormViewModel(project));
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
                return await ViewFormAsync(vm);

            if (!await ValidateShaftTemperatureAsync(vm))
                return await ViewFormAsync(vm);

            try
            {
                var userId = await GetCurrentUserAsync();
                var (success, error) = await _uow.projectRepository.UpdateProjectAsync(id, vm, userId);

                if (!success)
                {
                    TempData["Error"] = error ?? "Unable to update project.";
                    return await ViewFormAsync(vm);
                }

                // Recalculate since geometry/fluid inputs may have changed.
                var project = await _uow.projectRepository.GetProjectDetailAsync(id);
                var result = _calculationService.Calculate(project!.Vessel!, project.Impeller!, project.FluidProperties!);
                result.ProjectId = id;
                await _uow.calculationResultRepository.UpdateCalculationAsync(result);

                TempData["Success"] = "Project updated successfully.";
                return RedirectToAction(nameof(Results), new { id });
            }
            catch (Exception ex)
            {
                await _uow.exceptionHandlerRepository.SaveException(nameof(ProjectsController), nameof(Edit), ex);
                TempData["Error"] = "Something went wrong while updating the project.";
                return await ViewFormAsync(vm);
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
                                        new BreadcrumbItem { Text = $"{project.Name} - Results", IsActive = true });

                var comparison = BuildComparison(project);
                var vm = MapToDetailViewModel(project);
                vm.ShaftComparison = comparison;
                return View(vm);
            }
            catch (Exception ex)
            {
                await _uow.exceptionHandlerRepository.SaveException(nameof(ProjectsController), nameof(Results), ex);
                TempData["Error"] = "Unable to load results.";
                return RedirectToAction(nameof(Index));
            }
        }


        // POST: /Projects/GeneratePrediction/5
        [HttpPost]
        [ValidateAntiForgeryToken]
        public async Task<IActionResult> GeneratePrediction(int id)
        {
            try
            {
                var project = await _uow.projectRepository.GetProjectDetailAsync(id);
                if (project?.Vessel is null || project.Impeller is null ||
                    project.FluidProperties is null || project.CalculationResult is null)
                {
                    TempData["Error"] = "Project or calculation result not found.";
                    return RedirectToAction(nameof(Results), new { id });
                }

                var prediction = await _shaftPredictionService.PredictAsync(
                    project.Vessel, project.Impeller, project.FluidProperties, project.CalculationResult);

                var json = System.Text.Json.JsonSerializer.Serialize(prediction);
                var (success, error) = await _uow.calculationResultRepository
                    .SetPredictionAsync(id, json, prediction.GeneratedAtUtc);

                if (success)
                {
                    project.CalculationResult.PredictionJson = json;
                    var comparison = BuildComparison(project)!;

                    (success, error) = await _uow.calculationResultRepository.SetComparisonAsync(
                        id,
                        System.Text.Json.JsonSerializer.Serialize(comparison),
                        comparison.GeneratedAtUtc,
                        comparison.OverallPassed);
                }

                TempData[success ? "Success" : "Error"] = success ? "Shaft prediction generated." : error;
                return RedirectToAction(nameof(Results), new { id });
            }
            catch (InvalidOperationException ex)
            {
                await _uow.exceptionHandlerRepository.SaveException(nameof(ProjectsController), nameof(GeneratePrediction), ex);
                TempData["Error"] = "The shaft prediction service is not available right now. Please try again later.";
                return RedirectToAction(nameof(Results), new { id });
            }
            catch (Exception ex)
            {
                await _uow.exceptionHandlerRepository.SaveException(nameof(ProjectsController), nameof(GeneratePrediction), ex);
                TempData["Error"] = "Unable to generate shaft prediction.";
                return RedirectToAction(nameof(Results), new { id });
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
                    ? "3D model generation has been queued. Status will update automatically."
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

                // Refresh the comparison so the PDF carries the latest FEA and PINN values.
                var comparison = BuildComparison(project);
                if (comparison is not null && project.CalculationResult is not null)
                {
                    var comparisonJson = System.Text.Json.JsonSerializer.Serialize(comparison);
                    await _uow.calculationResultRepository.SetComparisonAsync(
                        id, comparisonJson, comparison.GeneratedAtUtc, comparison.OverallPassed);
                    project.CalculationResult.ComparisonJson = comparisonJson;
                    project.CalculationResult.ComparedAt = comparison.GeneratedAtUtc;
                    project.CalculationResult.ComparisonPassed = comparison.OverallPassed;
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

        // GET: /Projects/DownloadCadStep/5
        // Streams the FreeCAD-generated STEP file over HTTP. Needed because
        // the file lives on the server's local disk (CadIntegrationOptions
        // .OutputDirectory) � browsers block file:// links to local paths,
        // so the raw CadStepFilePath can never be used directly as an <a href>.
        public async Task<IActionResult> DownloadCadStep(int id)
        {
            try
            {
                var project = await _uow.projectRepository.GetProjectDetailAsync(id);
                if (project?.CalculationResult?.CadStepFilePath is null)
                {
                    TempData["Error"] = "No STEP file available for this project.";
                    return RedirectToAction(nameof(Results), new { id });
                }

                var bytes = await System.IO.File.ReadAllBytesAsync(project.CalculationResult.CadStepFilePath);
                return File(bytes, "application/step", $"{project.Name}-model.step");
            }
            catch (Exception ex)
            {
                await _uow.exceptionHandlerRepository.SaveException(nameof(ProjectsController), nameof(DownloadCadStep), ex);
                TempData["Error"] = "Unable to download STEP file.";
                return RedirectToAction(nameof(Results), new { id });
            }
        }

        // GET: /Projects/CadStatus/5
        // Lightweight JSON status endpoint polled by the Results page so the
        // UI can show Queued / Running / Completed / Failed without a reload.
        [HttpGet]
        public async Task<IActionResult> CadStatus(int id)
        {
            try
            {
                var project = await _uow.projectRepository.GetProjectDetailAsync(id);
                var result = project?.CalculationResult;
                if (result is null)
                    return NotFound(new { status = "NotFound", message = "No calculation result found." });

                return Json(new
                {
                    status = result.CadStatus.ToString(),
                    error = result.CadJobError,
                    hasStep = !string.IsNullOrEmpty(result.CadStepFilePath),
                    hasPreview = !string.IsNullOrEmpty(result.CadPreviewMeshPath)
                });
            }
            catch (Exception ex)
            {
                await _uow.exceptionHandlerRepository.SaveException(nameof(ProjectsController), nameof(CadStatus), ex);
                return StatusCode(500, new { status = "Error", message = "Unable to read model status." });
            }
        }

        // GET: /Projects/PreviewMesh/5
        // Serves the FreeCAD-generated OBJ preview mesh over HTTP (inline,
        // not as an attachment) so the browser-side Three.js OBJLoader can
        // fetch it via XHR � same file:// restriction as DownloadCadStep,
        // just returned without a Content-Disposition download prompt.
        public async Task<IActionResult> PreviewMesh(int id)
        {
            var project = await _uow.projectRepository.GetProjectDetailAsync(id);
            if (project?.CalculationResult?.CadPreviewMeshPath is null)
                return NotFound();

            var bytes = await System.IO.File.ReadAllBytesAsync(project.CalculationResult.CadPreviewMeshPath);
            return File(bytes, "text/plain");
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

        /// <summary>
        /// Builds the manual / FEA / PINN comparison from the stored prediction and FEA results.
        /// Returns null when the project has no calculation yet.
        /// </summary>
        private ShaftComparisonResult? BuildComparison(Project project)
        {
            if (project.Vessel is null || project.Impeller is null ||
                project.FluidProperties is null || project.CalculationResult is null)
                return null;

            ShaftPredictionResult? prediction = null;
            var predictionJson = project.CalculationResult.PredictionJson;
            if (!string.IsNullOrWhiteSpace(predictionJson))
            {
                try
                {
                    prediction = System.Text.Json.JsonSerializer.Deserialize<ShaftPredictionResult>(predictionJson);
                }
                catch (System.Text.Json.JsonException)
                {
                    prediction = null;
                }
            }

            var comparison = _calculationService.BuildShaftComparison(
                project.Vessel, project.Impeller, project.FluidProperties, project.CalculationResult, prediction);

            if (!comparison.AllChecksPassed && prediction is not null &&
     prediction.WithinTrustedRange && prediction.IsTrusted && comparison.AllQuantitiesAgree &&
     prediction.PredictedShaftDiameterMm > project.CalculationResult.RecommendedShaftDiameterMm)
            {
                project.CalculationResult.RecommendedShaftDiameterMm = prediction.PredictedShaftDiameterMm;
                comparison = _calculationService.BuildShaftComparison(
                    project.Vessel, project.Impeller, project.FluidProperties, project.CalculationResult, prediction);
            }

            return comparison;
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
                ImpellerMassKg = project.Impeller?.ImpellerMassKg ?? 20,
                DensityKgM3 = project.FluidProperties?.DensityKgM3 ?? 1000,
                ViscosityPaS = project.FluidProperties?.ViscosityPaS ?? 0.001,
                OperatingTemperatureC = project.FluidProperties?.OperatingTemperatureC ?? 30,
                ShaftMaterialId = project.Impeller?.ShaftMaterialId
            };
        }

        private async Task<IActionResult> ViewFormAsync(ProjectFormViewModel vm)
        {
            vm.ShaftMaterialOptions = await _uow.shaftMaterialRepository.GetAllActiveAsync();
            return View(vm);
        }

        private async Task<bool> ValidateShaftTemperatureAsync(ProjectFormViewModel vm)
        {
            if (vm.ShaftMaterialId is null) return true;

            var material = await _uow.shaftMaterialRepository.GetWithDeratingAsync(vm.ShaftMaterialId.Value);
            if (material is null)
            {
                ModelState.AddModelError(nameof(vm.ShaftMaterialId), "The selected shaft material was not found.");
                return false;
            }

            var temperature = (decimal)vm.OperatingTemperatureC;
            if (temperature < material.MinOperatingTempC || temperature > material.MaxOperatingTempC)
            {
                ModelState.AddModelError(nameof(vm.OperatingTemperatureC),
                    $"{material.DisplayName} is valid from {material.MinOperatingTempC:0.#} to " +
                    $"{material.MaxOperatingTempC:0.#} �C. Choose another material or a different temperature.");
                return false;
            }

            return true;
        }
    }
}