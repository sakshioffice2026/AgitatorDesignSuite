using System.Security.Claims;

namespace Agitator.Utilities.Helpers
{
    /// <summary>
    /// Shared helpers for pulling the current user id and tenant name off
    /// the authenticated ClaimsPrincipal, used by controllers'
    /// GetCurrentUserAsync/SetTenantNameAsync pattern.
    /// Reads claims manually rather than via ClaimsPrincipal.FindFirstValue,
    /// since that extension lives in the ASP.NET Core shared framework
    /// which this plain class library doesn't reference.
    /// </summary>
    public static class UserContextHelper
    {
        public static string GetUserId(ClaimsPrincipal user)
        {
            return user.Claims.FirstOrDefault(c => c.Type == ClaimTypes.NameIdentifier)?.Value
                ?? user.Identity?.Name
                ?? "demo-user";
        }

        public static string GetTenantId(ClaimsPrincipal user)
        {
            return user.Claims.FirstOrDefault(c => c.Type == "TenantId")?.Value
                ?? user.Identity?.Name
                ?? "demo-tenant";
        }
    }
}