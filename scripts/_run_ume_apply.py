from sqlalchemy import text

from netx_api.db import SessionLocal
from netx_api.ume_topology_apply import apply_ume_topology_to_fabric
from netx_api.ume_topology_world import ensure_ume_world_and_sbn_folders, get_world_view

db = SessionLocal()
try:
    print("apply…")
    s = apply_ume_topology_to_fabric(db)
    print("apply", s)
    w = ensure_ume_world_and_sbn_folders(db)
    print("world", w)
    v = get_world_view(db)
    print("view", v.id if v else None)
    n_xy = db.execute(text("select count(*) from topo_fabric_node where world_x is not null")).scalar()
    n_e = db.execute(text("select count(*) from topo_fabric_edge where status='active'")).scalar()
    n_f = db.execute(
        text("select count(*) from topo_folder where external_ref<>'' and external_ref<>'ume:world'")
    ).scalar()
    print("fabric_xy", n_xy, "active_edges", n_e, "sbn_folders", n_f)
finally:
    db.close()
