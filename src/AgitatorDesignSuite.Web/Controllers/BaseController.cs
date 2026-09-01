
using Agitator.Utilities.Helpers;
using Microsoft.AspNetCore.Mvc;

namespace AgitatorDesignSuite.Web.Controllers
{
    public abstract class BaseController : Controller
    {
        protected void SetBreadcrumb(params BreadcrumbItem[] items)
        {
            ViewData["Breadcrumb"] = items.ToList();
        }

        protected Task<string> GetCurrentUserAsync()
        {
            return Task.FromResult(UserContextHelper.GetUserId(User));
        }

        protected Task<string> SetTenantNameAsync()
        {
            var tenantId = UserContextHelper.GetTenantId(User);
            ViewData["TenantId"] = tenantId;
            return Task.FromResult(tenantId);
        }
    }
}