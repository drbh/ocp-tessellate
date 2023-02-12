#
# Copyright 2023 Bernhard Walter
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

from collections.abc import Iterable

from .cad_objects import (
    CoordSystem,
    OCP_Edges,
    OCP_Faces,
    OCP_Part,
    OCP_PartGroup,
    OCP_Vertices,
    get_instances,
    set_instances,
)
from .defaults import get_default, preset
from .mp_tessellator import get_mp_result, is_apply_result
from .ocp_utils import (
    BoundingBox,
    copy_shape,
    downcast,
    get_downcasted_shape,
    get_edges,
    get_location,
    get_rgba,
    get_tshape,
    get_tuple,
    identity_location,
    is_build123d_assembly,
    is_build123d_compound,
    is_build123d_shape,
    is_build123d,
    is_cadquery_assembly,
    is_cadquery_sketch,
    is_cadquery,
    is_compound,
    is_edge_list,
    is_face_list,
    is_solid_list,
    is_toploc_location,
    is_topods_compound,
    is_topods_edge,
    is_topods_face,
    is_topods_shape,
    is_topods_solid,
    is_topods_vertex,
    is_topods_wire,
    is_vector,
    is_vertex_list,
    is_wire_list,
    is_wrapped,
    make_compound,
    np_bbox,
    ocp_color,
    vertex,
)
from .utils import Color

EDGE_COLOR = "Silver"
THICK_EDGE_COLOR = "MediumOrchid"
VERTEX_COLOR = "MediumOrchid"
FACE_COLOR = "Violet"

OBJECTS = {"objs": [], "names": [], "colors": [], "alphas": []}

GROUP_NAME_LUT = {
    "OCP_Part": "Solids",
    "OCP_Faces": "Faces",
    "OCP_Edges": "Edges",
    "OCP_Vertices": "Vertices",
}


def _debug(msg):
    # print("DEBUG:", msg)
    pass


def web_color(name):
    wc = Color(name)
    return ocp_color(*wc.percentage)


def tessellate_group(group, kwargs=None, progress=None, timeit=False):
    if kwargs is None:
        kwargs = {}

    shapes = group.collect_shapes(
        "",
        None,
        deviation=preset("deviation", kwargs.get("deviation")),
        angular_tolerance=preset("angular_tolerance", kwargs.get("angular_tolerance")),
        edge_accuracy=preset("edge_accuracy", kwargs.get("edge_accuracy")),
        render_edges=preset("render_edges", kwargs.get("render_edges")),
        parallel=kwargs.get("parallel"),
        progress=progress,
        timeit=timeit,
    )
    states = group.to_state()

    return get_instances(), shapes, states


def combined_bb(shapes):
    def c_bb(shapes, bb):
        for shape in shapes["parts"]:
            if shape.get("parts") is None:
                if bb is None:
                    if shape["bb"] is None:
                        bb = BoundingBox()
                    else:
                        bb = BoundingBox(shape["bb"])
                else:
                    if shape["bb"] is not None:
                        bb.update(shape["bb"])

                # after updating the global bounding box, remove the local
                del shape["bb"]
            else:
                bb = c_bb(shape, bb)
        return bb

    bb = c_bb(shapes, None)
    return bb


def mp_get_results(shapes, progress):
    def walk(shapes):
        for shape in shapes["parts"]:
            if shape.get("parts") is None:
                if shape.get("type") == "shapes":
                    if is_apply_result(shape["shape"].get("result")):
                        mesh = get_mp_result(shape["shape"]["result"])
                        t = shape["shape"]["t"]
                        q = shape["shape"]["q"]
                        shape["shape"] = mesh
                        shape["bb"] = np_bbox(mesh["vertices"], t, q)

                    if progress is not None:
                        progress.update()
            else:
                walk(shape)

    walk(shapes)
    return shapes


def get_accuracies(shapes):
    def _get_accuracies(shapes, lengths):
        if shapes.get("parts"):
            for shape in shapes["parts"]:
                _get_accuracies(shape, lengths)
        elif shapes.get("type") == "shapes":
            accuracies[shapes["id"]] = shapes["accuracy"]

    accuracies = {}
    _get_accuracies(shapes, accuracies)
    return accuracies


def get_normal_len(render_normals, shapes, deviation):
    if render_normals:
        accuracies = get_accuracies(shapes)
        normal_len = max(accuracies.values()) / deviation * 4
    else:
        normal_len = 0

    return normal_len


def conv_sketch(cad_obj):
    cad_objs = []
    if cad_obj._faces:
        if not isinstance(cad_obj._faces, Iterable):
            faces = [cad_obj._faces]
        else:
            faces = cad_obj._faces
        cad_objs.extend([f.moved(loc).wrapped for f in faces for loc in cad_obj.locs])

    if cad_obj._wires:
        cad_objs.extend(
            [w.moved(loc).wrapped for w in cad_obj._wires for loc in cad_obj.locs]
        )

    if cad_obj._edges:
        cad_objs.extend(
            [e.moved(loc).wrapped for e in cad_obj._edges for loc in cad_obj.locs]
        )

    if cad_obj._selection:
        if is_toploc_location(cad_obj._selection[0].wrapped):
            objs = [
                make_compound(
                    [vertex((0, 0, 0)).Moved(loc.wrapped) for loc in cad_obj._selection]
                )
            ]
        else:
            objs = [
                make_compound(
                    [
                        e.moved(loc).wrapped
                        for e in cad_obj._selection
                        for loc in cad_obj.locs
                    ]
                )
            ]
        cad_objs.extend(objs)

    return cad_objs


def conv(cad_obj, obj_id=1, obj_name=None, obj_color=None, obj_alpha=1.0):
    if isinstance(cad_obj, OCP_PartGroup):
        return cad_obj

    elif isinstance(cad_obj, (OCP_Faces, OCP_Edges, OCP_Vertices)):
        pg = OCP_PartGroup([cad_obj], name=f"Group_{obj_id}")
        return pg

    default_color = get_default("default_color")

    if obj_name is None and hasattr(cad_obj, "label") and cad_obj.label != "":
        obj_name = cad_obj.label

    cad_objs = []

    # BuildPart, BuildSketch, BuildLine
    if is_build123d(cad_obj):
        _debug(f"build123d Builder {obj_id}: {type(cad_obj)}")
        cad_obj = getattr(cad_obj, cad_obj._obj_name)  # convert to direct API

    if is_build123d_compound(cad_obj):
        # build123d assembly
        if is_build123d_assembly(cad_obj):
            _debug(f"build123d Assembly {obj_id}: {type(cad_obj)}")
            cad_objs = []
            raise NotImplemented("build123d assemblies not implemented yet")

        # build123d Compound
        else:
            _debug(f"build123d Compound {obj_id}: {type(cad_obj)}")
            cad_objs = [downcast(obj.wrapped) for obj in cad_obj]

    elif is_build123d_shape(cad_obj):
        _debug(f"build123d Shape {obj_id}: {type(cad_obj)}")
        cad_objs = [downcast(cad_obj.wrapped)]

    elif is_cadquery_sketch(cad_obj):
        cad_objs = conv_sketch(cad_obj)

    elif is_cadquery(cad_obj):
        cad_objs = []
        for v in cad_obj.vals():
            if is_cadquery_sketch(v):
                obj = conv_sketch(v)

            elif is_vector(v.wrapped):
                obj = [vertex(v.wrapped)]

            else:
                obj = get_downcasted_shape(v.wrapped)

            cad_objs.extend(obj)

    elif is_wrapped(cad_obj):
        if is_vector(cad_obj.wrapped):
            cad_objs = [vertex(cad_obj.wrapped)]
        else:
            cad_objs = [cad_obj.wrapped]

    elif isinstance(cad_obj, Iterable):
        objs = list(cad_obj)
        if len(objs) > 0 and is_wrapped(objs[0]):
            # ShapeList
            _debug(f"build123d ShapeList {obj_id}: {type(cad_obj)}")
            cad_objs = [downcast(obj.wrapped) for obj in objs]
        else:
            raise ValueError("Empty list cannot be tessellated")

    elif is_topods_compound(cad_obj):
        _debug(f"CAD Obj {obj_id}: TopoDS Compound")

        # Get the highest level shape
        cad_objs = get_downcasted_shape(cad_obj)

    elif is_topods_shape(cad_obj):
        _debug(f"CAD Obj {obj_id}: TopoDS Shape")
        cad_objs = [downcast(cad_obj)]

    # Convert to PartGroup

    if is_solid_list(cad_objs):
        name = f"{obj_name if obj_name is not None else 'Solid'}_{obj_id}"
        return OCP_Part(
            cad_objs,
            name=name,
            color=get_rgba(obj_color, obj_alpha, Color(default_color)),
        )

    elif is_face_list(cad_objs):
        name = f"{obj_name if obj_name is not None else 'Face'}_{obj_id}"
        return OCP_Faces(
            cad_objs, name=name, color=get_rgba(obj_color, obj_alpha, Color(FACE_COLOR))
        )

    elif is_wire_list(cad_objs):
        edges = []
        for wire in cad_objs:
            edges.extend(get_edges(wire))

        name = f"{obj_name if obj_name is not None else 'Wire'}_{obj_id}"
        return OCP_Edges(
            edges,
            name=name,
            color=get_rgba(obj_color, 1.0, Color(THICK_EDGE_COLOR)),
            width=2,
        )

    elif is_edge_list(cad_objs):
        name = f"{obj_name if obj_name is not None else 'Edge'}_{obj_id}"
        return OCP_Edges(
            cad_objs,
            name=name,
            color=get_rgba(obj_color, 1.0, THICK_EDGE_COLOR),
            width=2,
        )

    elif is_vertex_list(cad_objs):
        name = f"{obj_name if obj_name is not None else 'Vertex'}_{obj_id}"
        return OCP_Vertices(
            cad_objs,
            name=name,
            color=get_rgba(obj_color, 1.0, THICK_EDGE_COLOR),
            size=6,
        )

    else:
        raise RuntimeError("You shouldn't get here!")


def get_instance(obj, obj_id, name, rgba, instances):
    is_instance = False
    part = None
    for i, ref in enumerate(instances):
        if ref[0] == get_tshape(obj):
            part = OCP_Part(
                {"ref": i},
                f"{name}_{obj_id}",
                rgba,
            )
            is_instance = True

    if not is_instance:
        part = conv(obj, obj_id, name, rgba[:3], rgba[3])
        if not isinstance(part, OCP_PartGroup):
            instances.append((get_tshape(obj), part.shape[0]))
            part = OCP_Part(
                {"ref": len(instances) - 1},
                part.name,
                rgba,
            )

    return part


def relocate(obj):
    loc = get_location(obj)

    if loc is None or not hasattr(obj, "wrapped"):
        return obj, None

    obj = copy_shape(obj)

    tshape = get_tshape(obj)
    obj.wrapped.Move(loc.Inverted())
    obj.wrapped.TShape(tshape)

    return obj, loc


def _to_assembly(
    *cad_objs,
    names=None,
    colors=None,
    alphas=None,
    name="Group",
    render_mates=None,
    mate_scale=1,
    default_color=None,
    show_parent=False,
    loc=None,
    grp_id=0,
    mates=None,
    instances=None,
):
    if names is None:
        names = [None] * len(cad_objs)

    if colors is None:
        colors = [None] * len(cad_objs)

    if alphas is None:
        alphas = [None] * len(cad_objs)

    if default_color is None:
        default_color = (
            get_default("default_color") if default_color is None else default_color
        )

    if instances is None:
        instances = []

    pg = OCP_PartGroup([], f"{name}_{grp_id}", identity_location())

    obj_id = 0

    for obj_name, obj_color, obj_alpha, cad_obj in zip(names, colors, alphas, cad_objs):
        if hasattr(cad_obj, "color") and cad_obj.color is not None:
            *color, alpha = get_rgba(cad_obj.color, obj_alpha, Color(default_color))
        else:
            color, alpha = obj_color, obj_alpha
        rgba = get_rgba(color, alpha, Color(default_color))

        if is_cadquery_assembly(cad_obj):
            #
            # Iterate over CadQuery Assembly
            #

            pg.name = cad_obj.name
            pg.loc = get_location(cad_obj, as_none=False)

            if cad_obj.obj is not None:
                part = get_instance(cad_obj.obj, obj_id, pg.name, rgba, instances)
                pg.add(part)
                obj_id += 1

            top_level_mates = None
            if render_mates and hasattr(cad_obj, "mates") and cad_obj.mates is not None:
                top_level_mates = cad_obj.mates if mates is None else mates
                pg2 = OCP_PartGroup(
                    [
                        CoordSystem(
                            name,
                            get_tuple(mate_def.mate.origin),
                            get_tuple(mate_def.mate.x_dir),
                            get_tuple(mate_def.mate.y_dir),
                            get_tuple(mate_def.mate.z_dir),
                            mate_scale,
                        )
                        for name, mate_def in top_level_mates.items()
                        if mate_def.assembly == cad_obj
                    ],
                    name="mates",
                    loc=identity_location(),  # mates inherit the parent location, so actually add a no-op
                )
                if pg2.objects:
                    pg.add(pg2)

            for child in cad_obj.children:
                grp_id += 1
                part, instances = _to_assembly(
                    child,
                    loc=loc,
                    grp_id=grp_id,
                    default_color=default_color,
                    names=[obj_name],
                    colors=[obj_color],
                    alphas=[obj_alpha],
                    mates=top_level_mates,
                    render_mates=render_mates,
                    mate_scale=mate_scale,
                    instances=instances,
                )
                pg.add(part)
                obj_id += 1

        elif is_compound(cad_obj):
            #
            # Iterate over Compound (includes build123d assemblies)
            #

            if (
                hasattr(cad_obj, "label")
                and cad_obj.label is not None
                and cad_obj.label != ""
            ):
                pg.name = cad_obj.label

            done = False
            if is_build123d_assembly(cad_obj):
                children = cad_obj.children
            else:
                children = list(cad_obj)
                cw = [c.wrapped for c in children]
                if (
                    is_face_list(cw)
                    or is_edge_list(cw)
                    or is_vertex_list(cw)
                    or is_wire_list(cw)
                ):
                    # Don't explode homogenous lists
                    part = conv(children, obj_id, obj_name, color, alpha)
                    pg.add(part)
                    done = True

            if not done:
                for child in children:
                    grp_id += 1
                    part, instances = _to_assembly(
                        child,
                        grp_id=grp_id,
                        default_color=default_color,
                        names=[obj_name],
                        colors=[obj_color],
                        alphas=[obj_alpha],
                        render_mates=render_mates,
                        mate_scale=mate_scale,
                        instances=instances,
                    )
                    pg.add(part)

        elif is_cadquery_sketch(cad_obj):
            for child in conv_sketch(cad_obj):
                part, instances = _to_assembly(
                    child,
                    grp_id=grp_id,
                    default_color=default_color,
                    names=[obj_name],
                    colors=[obj_color],
                    alphas=[obj_alpha],
                    render_mates=render_mates,
                    mate_scale=mate_scale,
                    instances=instances,
                )
                pg.add(part)

                grp_id += 1
        else:
            #
            # Render non iterable objects
            #

            if hasattr(cad_obj, "obj"):
                cad_obj = cad_obj.obj

            if hasattr(cad_obj, "label") and cad_obj.label != "":
                obj_name = cad_obj.label

            is_solid = False

            if hasattr(cad_obj, "wrapped"):
                solids = get_downcasted_shape(cad_obj.wrapped)
                # TODO: what to do with mixed compounds
                is_solid = all([is_topods_solid(solid) for solid in solids])

            parent = None
            if show_parent and hasattr(cad_obj, "parent"):
                parent = cad_obj.parent

            loc = None
            if is_solid:
                cad_obj, loc = relocate(cad_obj)

            # TODO Fix parent
            if parent is not None:
                pg.add(conv(parent, obj_id, "parent", None, None))
                pg.objects[0].state_faces = 0

            if is_solid and loc is not None:
                # create a partgroup and move part location into it
                name = f"{'Solids' if obj_name is None else obj_name}_{grp_id}"
                grp_id += 1
                pg2 = OCP_PartGroup([], name=name, loc=loc)
                part = get_instance(cad_obj, 0, name, rgba, instances)
                pg.loc = identity_location()
                pg2.add(part)

                # add additional partgroup
                pg.add(pg2)

            else:
                part = conv(cad_obj, obj_id, obj_name, color, alpha)

                pg.add(part)  # no clear way to relocated

        obj_id += 1

        if pg.loc is None:
            raise RuntimeError("location is None")
            # pg.loc = identity_location()

    if len(pg.objects) == 1 and isinstance(pg.objects[0], OCP_PartGroup):
        pg = pg.objects[0]

    return pg, instances


def to_assembly(
    *cad_objs,
    names=None,
    colors=None,
    alphas=None,
    name="Group",
    render_mates=None,
    mate_scale=1,
    default_color=None,
    show_parent=False,
    loc=None,
    grp_id=0,
    mates=None,
    instances=None,
):
    pg, instances = _to_assembly(
        *cad_objs,
        names=names,
        colors=colors,
        alphas=alphas,
        name=name,
        render_mates=render_mates,
        mate_scale=mate_scale,
        default_color=default_color,
        show_parent=show_parent,
        loc=loc,
        grp_id=grp_id,
        mates=mates,
        instances=instances,
    )
    set_instances([instance[1] for instance in instances])
    return pg
