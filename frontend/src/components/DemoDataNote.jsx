import { useAuth } from '../AuthContext';
import '../styles/strategy.css';

// The demo buyer has no "demo" flag in the database: the marker is the team
// name suffix "(demo)" (docs/demo_api/demo_buyer.md). Its prices and volumes
// are made up, so pages that show its money say so.
export function useIsDemoTeam() {
  const { teams, activeTeamId } = useAuth();
  const name = (teams || []).find(t => t.id === activeTeamId)?.name || '';
  return name.trim().endsWith('(demo)');
}

// Small "Illustrative demo data" pill for a page header, styled like the
// Strategy landing's (.st-illus). Renders nothing for other teams.
export default function DemoDataNote({ style }) {
  const isDemo = useIsDemoTeam();
  if (!isDemo) return null;
  return (
    <span className="st-illus" style={style} title="This team's prices and volumes are made up for the demo">
      Illustrative demo data
    </span>
  );
}
