

using Agitator.Core.Entities;
using Agitator.Models.ViewModel;

namespace Agitator.Repositories.Contracts
{
    public interface IProjectRepository
    {
        Task<List<ProjectListItemViewModel>> GetProjectIndexDataAsync(string tenantId);
        Task<Project?> GetProjectDetailAsync(int id);
        Task<(bool success, string? error, int projectId)> CreateProjectAsync(ProjectFormViewModel vm, string tenantId, string? userId);
        Task<(bool success, string? error)> UpdateProjectAsync(int id, ProjectFormViewModel vm, string? userId);
        Task<(bool success, string? error)> DeleteProjectAsync(int id);
        Task<bool> IsProjectExistsAsync(int id);
        Task<bool> CanEditProjectAsync(int id, string tenantId);
    }
}