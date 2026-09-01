namespace Agitator.Core.Entities
{
    /// <summary>Persisted log of unhandled exceptions caught by controllers, written via ExceptionHandlerRepository.</summary>
    public class ExceptionHandler
    {
        public int Id { get; set; }
        public string ClassName { get; set; } = string.Empty;
        public string MethodName { get; set; } = string.Empty;
        public string ErrorMessage { get; set; } = string.Empty;
        public string? StackTrace { get; set; }
        public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    }
}