import { useEffect, useState } from "react";
import { api, BuildingViewResponse, Row } from "../api";
import { DrawerRequest } from "./SourceDrawer";
import Table from "./Table";

// For one building and date: each shift's assigned staff (punched in/out, or absent and why),
// who covered, and the contracted posts. A snapshot as of the last pipeline run.
export default function BuildingView({ open }: { open: (r: DrawerRequest) => void }) {
  const [buildings, setBuildings] = useState<Row[]>([]);
  const [building, setBuilding] = useState("");
  const [day, setDay] = useState("");
  const [data, setData] = useState<BuildingViewResponse | null>(null);

  useEffect(() => {
    api.buildings().then((b) => {
      setBuildings(b);
      if (b.length) setBuilding(String(b[0].building));
    });
  }, []);

  useEffect(() => {
    if (!building) return;
    api.buildingView(building, day || "1900-01-01").then((d) => {
      if (!day && d.dates.length) setDay(String(d.dates[0]).slice(0, 10));
      else setData(d);
    });
  }, [building, day]);

  const posts = data?.posts[0];
  const openSource = (r: Row) => {
    const ref = String(r.source_ref ?? "");
    if (r.posting_id) open({ kind: "source", source: "dispatch", ref: String(r.posting_id) });
    else if (ref.includes(".csv#")) open({ kind: "source", source: "time_attendance", ref, title: `Punches · ${r.name}` });
    else if (ref.includes(":")) {
      const [source, ...rest] = ref.split(":");
      open({ kind: "source", source, ref: rest.join(":") });
    } else if (r.hr_id) open({ kind: "source", source: "hr_roster", ref: String(r.hr_id) });
  };

  return (
    <section>
      <div className="controls">
        <select value={building} onChange={(e) => setBuilding(e.target.value)}>
          {buildings.map((b) => <option key={String(b.building)}>{String(b.building)}</option>)}
        </select>
        <select value={day} onChange={(e) => setDay(e.target.value)}>
          {(data?.dates ?? []).map((d) => {
            const v = String(d).slice(0, 10);
            return <option key={v} value={v}>{new Date(v + "T12:00").toDateString()}</option>;
          })}
        </select>
        {posts && (
          <span className="muted">
            {String(posts.client)} · {String(posts.pricing).replace("_", " ")}
            {posts.coverage_billable ? " · coverage billable" : ""}
          </span>
        )}
      </div>
      {data && ["day", "night"].map((shift) => {
        const people = data.people.filter((p) => p.shift === shift);
        const assigned = people.filter((p) => p.role === "assigned");
        const worked = people.filter((p) => p.status === "worked" || String(p.status).startsWith("filled")).length;
        const contracted = posts ? Number(shift === "day" ? posts.day_posts : posts.night_posts) : 0;
        return (
          <div key={shift} className="shift">
            <h2>
              {shift === "day" ? "Day 07:00–15:00" : "Night 16:00–00:00"}
              <span className="badge">{assigned.length} assigned · {worked} on site · {contracted} contracted posts</span>
            </h2>
            <Table
              rows={people}
              columns={["name", "role", "status", "clock_in", "clock_out", "posting_id"]}
              onRowClick={openSource}
              render={{ status: (r) => <span className={`status ${String(r.status).split(":")[0].replace(/\W/g, "")}`}>{String(r.status)}</span> }}
            />
          </div>
        );
      })}
    </section>
  );
}
