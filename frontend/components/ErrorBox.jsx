// A red box with a readable problem (an ApiError's message) and an optional "Try again" button.
export default function ErrorBox({ error, message, onRetry }) {
  const text = message || error?.message || String(error || "Something went wrong.");
  return (
    <div role="alert" className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-900
      dark:border-red-800 dark:bg-red-950/40 dark:text-red-200">
      <p>⚠️ {text}</p>
      {onRetry && (
        <button onClick={onRetry} className="mt-3 rounded-md bg-red-600 px-3 py-1.5 font-semibold text-white hover:bg-red-700">
          Try again
        </button>
      )}
    </div>
  );
}
