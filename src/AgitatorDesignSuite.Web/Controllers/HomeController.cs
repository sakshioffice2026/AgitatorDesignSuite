using Agitator.Repositories.Contracts;
using Agitator.Utilities.Helpers;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace AgitatorDesignSuite.Web.Controllers
{
   
    public class HomeController : BaseController
    {
        private readonly IUnitOfWork _uow;
        private readonly ILogger<HomeController> _logger;

        public HomeController(IUnitOfWork uow, ILogger<HomeController> logger)
        {
            _uow = uow;
            _logger = logger;
        }

        public async Task<IActionResult> Index()
        {
            try
            {
                var tenantId = await SetTenantNameAsync();
                SetBreadcrumb(new BreadcrumbItem { Text = "Dashboard", IsActive = true });

                var projects = await _uow.projectRepository.GetProjectIndexDataAsync(tenantId);

                _logger.LogInformation("Loaded dashboard for tenant {TenantId} with {Count} projects", tenantId, projects.Count);
                return View(projects);
            }
            catch (Exception ex)
            {
                await _uow.exceptionHandlerRepository.SaveException(nameof(HomeController), nameof(Index), ex);
                TempData["Error"] = "Something went wrong loading the dashboard.";
                return View(new List<Agitator.Models.ViewModel.ProjectListItemViewModel>());
            }
        }
    }
}