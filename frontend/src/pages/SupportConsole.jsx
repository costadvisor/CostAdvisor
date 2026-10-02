import { useEffect, useState } from 'react';
import api from '../api';
import SupportAdminTab from '../components/SupportAdminTab';

/**
 * The support staff console, on its own route.
 *
 * It already existed as a tab inside /admin, but that page hard-denies anyone
 * who is not a super admin — so the **Support Agent** platform role, which the
 * support migration seeded precisely so staff access would not require super
 * admin, had full API access and no way to reach any of it. This route is the
 * missing door.
 *
 * Deliberately NOT "show /admin to support agents": every other tab there
 * (users, teams, audit log, platform settings) calls super-admin-only
 * endpoints, so a support agent sent to that page would get a screen of failed
 * requests, and widening its gate would put genuinely privileged surfaces one
 * render-condition away from a non-super-admin. A separate route keeps /admin
 * exactly as restricted as it was.
 *
 * The same `SupportAdminTab` component backs both, so there is one console, not
 * a second copy that drifts.
 */
export default function SupportConsole() {
  // `staff` is tri-state on purpose: null means "not answered yet". Rendering
  // the denial while the probe is in flight would flash Access Denied at every
  // legitimate staff member on every load.
  const [staff, setStaff] = useState(null);

  useEffect(() => {
    let cancelled = false;
    api.get('/api/support/is-staff')
      .then(({ data }) => { if (!cancelled) setStaff(!!data.is_staff); })
      // A failed probe is not authorisation — deny.
      .catch(() => { if (!cancelled) setStaff(false); });
    return () => { cancelled = true; };
  }, []);

  if (staff === null) {
    return (
      <div className="ca-page ca-fade-in">
        <div className="ca-h1">Support Console</div>
        <p className="ca-subtitle">Checking access…</p>
      </div>
    );
  }

  if (!staff) {
    return (
      <div className="ca-page ca-fade-in">
        <div className="ca-h1">Access Denied</div>
        <p className="ca-subtitle">
          The support console is for support staff. Ask an administrator for the
          Support Agent role.
        </p>
      </div>
    );
  }

  return (
    <div className="ca-page ca-fade-in">
      <div className="ca-h1">Support Console</div>
      <p className="ca-subtitle">
        Customer threads, canned responses, and the knowledge base.
      </p>
      <SupportAdminTab />
    </div>
  );
}
