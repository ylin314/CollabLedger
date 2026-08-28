import { createHashRouter } from "react-router-dom";
import App, { AppErrorBoundary } from "../App";

export const router = createHashRouter([
  {
    path: "/classrooms",
    element: (<AppErrorBoundary><App /></AppErrorBoundary>),
  },
  {
    path: "/login",
    element: (
      <AppErrorBoundary>
        <App />
      </AppErrorBoundary>
    ),
  },
  {
    path: "/invite/:inviteCode",
    element: (
      <AppErrorBoundary>
        <App />
      </AppErrorBoundary>
    ),
  },
  {
    path: "/projects/new",
    element: (
      <AppErrorBoundary>
        <App />
      </AppErrorBoundary>
    ),
  },
  {
    path: "/projects/:projectId/:page?",
    element: (
      <AppErrorBoundary>
        <App />
      </AppErrorBoundary>
    ),
  },
  {
    path: "*",
    element: (
      <AppErrorBoundary>
        <App />
      </AppErrorBoundary>
    ),
  },
]);
