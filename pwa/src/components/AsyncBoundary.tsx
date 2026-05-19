import { JSX, ErrorBoundary, Suspense } from "solid-js";

interface Props {
  children: JSX.Element;
  fallback?: JSX.Element;
  errorFallback?: (err: Error, reset: () => void) => JSX.Element;
}

export default function AsyncBoundary(props: Props) {
  return (
    <ErrorBoundary
      fallback={(err, reset) =>
        props.errorFallback ? (
          props.errorFallback(err as Error, reset)
        ) : (
          <div class="p-4 text-sisoul-danger text-sm font-mono space-y-2">
            <p class="font-semibold">加载失败</p>
            <p class="text-sisoul-muted">{String(err)}</p>
            <button
              class="text-sisoul-accent underline"
              onClick={reset}
            >
              重试
            </button>
          </div>
        )
      }
    >
      <Suspense
        fallback={
          props.fallback ?? (
            <div class="p-8 text-sisoul-muted text-sm font-mono animate-pulse">
              加载中...
            </div>
          )
        }
      >
        {props.children}
      </Suspense>
    </ErrorBoundary>
  );
}
