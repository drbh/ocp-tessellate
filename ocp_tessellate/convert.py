from ocp_tessellate.ocp_utils import *
from ocp_tessellate.utils import make_unique, Timer
from ocp_tessellate.cad_objects import OcpGroup, OcpObject, CoordAxis, CoordSystem
from ocp_tessellate.tessellator import (
    convert_vertices,
    discretize_edges,
    tessellate,
    compute_quality,
)
from ocp_tessellate.defaults import get_default, preset


DEBUG = True


def _debug(msg, name=None, prefix="debug:", eol="\n"):
    if name is None:
        print(f"{prefix} {msg}", end=eol)
    else:
        print(f"{prefix} {msg} ('{name}')", end=eol)


def class_name(obj):
    return obj.__class__.__name__


def type_name(obj):
    return class_name(obj).split("_")[1]


def get_name(obj, name, default):
    if name is None:
        if hasattr(obj, "name") and obj.name is not None and obj.name != "":
            name = obj.name
        elif hasattr(obj, "label") and obj.label is not None and obj.label != "":
            name = obj.label
        else:
            name = default
    return name


def get_kind(obj):
    kinds = {
        "TopoDS_Edge": "edge",
        "TopoDS_Face": "face",
        "TopoDS_Shell": "face",
        "TopoDS_Solid": "solid",
        "TopoDS_Vertex": "vertex",
        "TopoDS_Wire": "edge",
    }
    return kinds.get(class_name(obj))


def get_color(obj, color=None, alpha=None):
    default_colors = {
        "TopoDS_Edge": "MediumOrchid",
        "TopoDS_Face": "Violet",
        "TopoDS_Shell": "Violet",
        "TopoDS_Solid": (232, 176, 36),
        "TopoDS_Vertex": "MediumOrchid",
        "TopoDS_Wire": "MediumOrchid",
    }
    if color is not None:
        col_a = Color(color)

    elif hasattr(obj, "color") and obj.color is not None:
        col_a = Color(obj.color)

    # else return default color
    col_a = Color(default_colors.get(class_name(unwrap(obj))))
    if alpha is not None:
        col_a.a = alpha

    return col_a


def unwrap(obj):
    if hasattr(obj, "wrapped"):
        return obj.wrapped
    elif isinstance(obj, (list, tuple)):
        return [x.wrapped for x in obj]
    return obj


def conv(): ...


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


# TODOs:
# - render mates
# - render joints
# - render parent
# - render normals
# - CadQuery objects
# - CadQuery assemblies


class OcpConverter:
    def __init__(self):
        self.instances = []
        self.ocp = None

    def get_instance(self, obj, kind, cache_id, name, color, alpha, progress=None):
        is_instance = False
        ocp_obj = None

        obj, loc = relocate(obj)

        # check if the same instance is already available
        for i, instance in enumerate(self.instances):
            if instance[0] == get_tshape(obj):
                # create a referential OcpObject
                ocp_obj = OcpObject(
                    kind,
                    ref=i,
                    name=name,
                    loc=loc,
                    color=color,
                    alpha=alpha,
                    cache_id=cache_id,
                )
                # and stop the loop
                is_instance = True

                if progress is not None:
                    progress.update("-")

                break

        if not is_instance:
            ref = len(self.instances)
            # append the new instance
            self.instances.append((get_tshape(obj), obj))
            # and create a referential OcpObject
            ocp_obj = OcpObject(
                kind,
                ref=ref,
                name=name,
                loc=loc,
                color=color,
                alpha=alpha,
                cache_id=cache_id,
            )

        return ocp_obj

    def unify(self, objs, name, color, alpha):
        if len(objs) == 1:
            ocp_obj = unwrap(objs[0])
            kind = get_kind(ocp_obj)
        else:
            objs = unwrap(objs)
            ocp_obj = make_compound(objs)
            kind = get_kind(objs[0])

        if kind in ("solid", "face"):
            return self.get_instance(
                ocp_obj, kind, id(ocp_obj), name, get_color(objs[0], color), alpha
            )
        return OcpObject(
            kind, obj=ocp_obj, name=name, color=get_color(objs[0], color), alpha=alpha
        )

    def to_ocp(
        self,
        *cad_objs,
        names=None,
        colors=None,
        alphas=None,
        loc=None,
        render_mates=None,
        render_joints=None,
        helper_scale=1,
        default_color=None,
        show_parent=False,
        sketch_local=False,
        instances=None,
    ):
        group = OcpGroup()

        # ============================= Validate parameters ============================= #

        if names is None:
            names = [None] * len(cad_objs)
        else:
            names = make_unique(names)
            if len(names) != len(cad_objs):
                raise ValueError("Length of names does not match the number of objects")

        if colors is None:
            colors = [None] * len(cad_objs)
        if len(colors) != len(cad_objs):
            raise ValueError("Length of colors does not match the number of objects")

        if alphas is None:
            alphas = [None] * len(cad_objs)
        if len(alphas) != len(cad_objs):
            raise ValueError(
                "Length of alpha values does not match the number of objects"
            )

        if default_color is None:
            default_color = (
                get_default("default_color") if default_color is None else default_color
            )

        if instances is None:
            instances = []

        for cad_obj, obj_name, obj_color, obj_alpha in zip(
            cad_objs, names, colors, alphas
        ):

            # ================================= Prepare ================================= #

            # Convert build123d BuildPart, BuildSketch, BuildLine to topology object
            if is_build123d(cad_obj):
                if DEBUG:
                    _debug(
                        "Convert build123d builder object to topology object", obj_name
                    )
                obj = cad_obj._obj

            # build123d Plane
            elif is_build123d_plane(cad_obj) and hasattr(cad_obj, "location"):
                if DEBUG:
                    _debug("Map plane to its location", obj_name)
                obj = cad_obj.location

            # Use input object
            else:
                obj = cad_obj

            # ================================== Loops ================================== #

            # build123d ShapeList (needs to be handled before the generic tuple/list case)
            if is_build123d_shapelist(obj):
                if DEBUG:
                    _debug("build123d ShapeList", obj_name)
                objs = unwrap(obj)
                ocp_obj = OcpObject(
                    get_kind(objs[0]),
                    obj=make_compound(objs),
                    name=get_name(obj, obj_name, "ShapeList"),
                )

            # Generic iterables (tuple, list) or mixed type compounds
            elif isinstance(obj, (list, tuple)) or (
                is_compound(obj) and is_mixed_compound(obj)
            ):
                kind = "List" if isinstance(obj, (list, tuple)) else "Mixed Compound"
                if DEBUG:
                    _debug(kind, obj_name)
                name = get_name(obj, obj_name, kind.split(" ")[-1])
                ocp_obj = OcpGroup(name=name)
                for i, el in enumerate(obj):
                    result = self.to_ocp(
                        el,
                        names=[f"{name}[{i}]"],
                        sketch_local=sketch_local,
                        instances=instances,
                    )
                    ocp_obj.add(result)

                if ocp_obj.length > 1:
                    ocp_obj.make_unique_names()

            # Dicts
            elif isinstance(obj, dict):
                if DEBUG:
                    _debug("dict", obj_name)
                ocp_obj = OcpGroup(name=obj_name)
                for name, el in obj.items():
                    result = self.to_ocp(
                        el,
                        names=[name],
                        sketch_local=sketch_local,
                        instances=instances,
                    )
                    ocp_obj.add(result)

            # =============================== Assemblies ================================ #

            elif is_build123d_assembly(cad_obj):
                if DEBUG:
                    _debug("build123d Assembly", obj_name)
                name = get_name(obj, obj_name, "Assembly")
                ocp_obj = OcpGroup(name=name, loc=get_location(obj, as_none=False))

                for child in obj.children:
                    sub_obj = self.to_ocp(
                        child, helper_scale=helper_scale, instances=instances
                    )
                    if isinstance(sub_obj, OcpGroup):
                        if sub_obj.length == 1:
                            if sub_obj.objects[0].loc is None:
                                sub_obj.objects[0].loc = sub_obj.loc
                            else:
                                sub_obj.objects[0].loc = (
                                    sub_obj.loc * sub_obj.objects[0].loc
                                )
                            sub_obj = sub_obj.objects[0]

                    ocp_obj.add(sub_obj)

            # =============================== Conversions =============================== #

            # bild123d BuildPart().part
            elif is_build123d_part(obj):
                if DEBUG:
                    _debug("build123d part", obj_name)
                objs = obj.solids()
                name = get_name(obj, obj_name, "Solid" if len(objs) == 1 else "Solids")
                ocp_obj = self.unify(objs, name, obj_color, obj_alpha)

            # build123d BuildSketch().sketch
            elif is_build123d_sketch(obj):
                if DEBUG:
                    _debug("build123d Sketch", obj_name)
                objs = obj.faces()
                name = get_name(obj, obj_name, "Face" if len(objs) == 1 else "Faces")
                ocp_obj = self.unify(objs, name, obj_color, obj_alpha)

                if sketch_local:
                    ocp_obj.name = "sketch"
                    ocp_obj = OcpGroup([ocp_obj], name=name)
                    obj_local = cad_obj.sketch_local
                    objs = obj_local.faces()
                    ocp_obj.add(self.unify(objs, "sketch_local", obj_color, obj_alpha))

            # build123d BuildLine().line
            elif is_build123d_curve(obj):
                if DEBUG:
                    _debug("build123d Curve", obj_name)
                objs = obj.edges()
                name = get_name(obj, obj_name, "Edge" if len(objs) == 1 else "Edges")
                ocp_obj = self.unify(objs, name, obj_color, obj_alpha)

            # build123d Shape, Compound, Edge, Face, Shell, Solid, Vertex, Wire
            elif is_build123d_shape(obj):
                if DEBUG:
                    _debug(f"build123d Shape", obj_name, eol="")
                objs = get_downcasted_shape(obj.wrapped)
                name = get_name(obj, obj_name, type_name(objs[0]))
                ocp_obj = self.unify(objs, name, obj_color, obj_alpha)
                if DEBUG:
                    _debug(class_name(ocp_obj.obj), prefix="")

            # TopoDS_Shape, TopoDS_Compound, TopoDS_Edge, TopoDS_Face, TopoDS_Shell,
            # TopoDS_Solid, TopoDS_Vertex, TopoDS_Wire
            elif is_topods_shape(obj):
                if DEBUG:
                    _debug("TopoDS_Shape", obj_name)
                objs = get_downcasted_shape(obj)
                name = get_name(obj, obj_name, type_name(objs[0]))
                ocp_obj = self.unify(objs, name, obj_color, obj_alpha)

            # build123d Location or TopLoc_Location
            elif is_build123d_location(obj) or is_toploc_location(obj):
                if DEBUG:
                    _debug("build123d Location or TopLoc_Location", obj_name)
                coord = get_location_coord(
                    obj.wrapped if is_build123d_location(obj) else obj
                )
                name = get_name(obj, obj_name, "Location")
                ocp_obj = CoordSystem(
                    name,
                    coord["origin"],
                    coord["x_dir"],
                    coord["z_dir"],
                    size=helper_scale,
                )

            # build123d Axis
            elif is_build123d_axis(obj):
                if DEBUG:
                    _debug("build123d Axis", obj_name)
                coord = get_axis_coord(obj.wrapped)
                name = get_name(obj, obj_name, "Axis")
                ocp_obj = CoordAxis(
                    name,
                    coord["origin"],
                    coord["z_dir"],
                    size=helper_scale,
                )

            else:
                raise ValueError(f"Unknown object type: {obj}")

            group.add(ocp_obj)

        # if group.length == 1:
        #     return group.objects[0]

        group.make_unique_names()
        return group


def to_assembly(
    *cad_objs,
    names=None,
    colors=None,
    alphas=None,
    render_mates=None,
    render_joints=None,
    helper_scale=1,
    default_color=None,
    show_parent=False,
    show_sketch_local=True,
    loc=None,
    mates=None,
    instances=None,
    progress=None,
):
    converter = OcpConverter()
    ocp = converter.to_ocp(
        *cad_objs,
        names=names,
        colors=colors,
        alphas=alphas,
        loc=loc,
        render_mates=render_mates,
        render_joints=render_joints,
        helper_scale=helper_scale,
        default_color=default_color,
        show_parent=show_parent,
        sketch_local=show_sketch_local,
        instances=instances,
    )
    instances = [i[1] for i in converter.instances]
    return ocp, instances


def tessellate_group(group, instances, kwargs=None, progress=None, timeit=False):
    def add_bb(shapes):
        for shape in shapes["parts"]:
            if shape.get("parts") is None:
                if shape["type"] == "shapes":
                    ind = shape["shape"]["ref"]
                    with Timer(
                        timeit,
                        f"instance({ind})",
                        "create bounding boxes:     ",
                        2,
                    ) as t:
                        shape["bb"] = np_bbox(
                            meshed_instances[ind]["vertices"],
                            *shape["loc"],
                        )
            else:
                add_bb(shape)

    if kwargs is None:
        kwargs = {}

    mapping, shapes = group.collect(
        "",
        instances,
        None,
    )
    states = group.to_state()

    meshed_instances = []

    deviation = preset("deviation", kwargs.get("deviation"))
    angular_tolerance = preset("angular_tolerance", kwargs.get("angular_tolerance"))
    edge_accuracy = preset("edge_accuracy", kwargs.get("edge_accuracy"))
    render_edges = preset("render_edges", kwargs.get("render_edges"))

    for i, instance in enumerate(instances):
        with Timer(timeit, f"instance({i})", "compute quality:", 2) as t:
            shape = instance
            # A first rough estimate of the bounding box.
            # Will be too large, but is sufficient for computing the quality
            # location is not relevant here
            bb = bounding_box(shape, loc=None, optimal=False)
            quality = compute_quality(bb, deviation=deviation)
            t.info = str(bb)

        with Timer(timeit, f"instance({i})", "tessellate:     ", 2) as t:
            mesh = tessellate(
                shape,
                id(shape),
                deviation=deviation,
                quality=quality,
                angular_tolerance=angular_tolerance,
                debug=timeit,
                compute_edges=render_edges,
                progress=progress,
                shape_id="n/a",
            )
            meshed_instances.append(mesh)
            t.info = (
                f"{{quality:{quality:.4f}, angular_tolerance:{angular_tolerance:.2f}}}"
            )
    add_bb(shapes)

    return meshed_instances, shapes, states, mapping
