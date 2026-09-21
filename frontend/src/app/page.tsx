import { Workspace } from "@/components/workspace";
import { RequireSession } from "@/lib/session";

export default function Home() {
  // The gate decides which screen to render, not what data anyone may see: the API
  // authenticates every request on its own and answers 401 regardless of what the
  // client believes.
  return (
    <RequireSession>
      <Workspace />
    </RequireSession>
  );
}
