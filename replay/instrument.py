from __future__ import annotations

import ast


class NameCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: list[str] = []
        self._bound_scopes: list[set[str]] = [set()]

    def visit_Name(self, node: ast.Name) -> None:
        if (
            isinstance(node.ctx, ast.Load)
            and not self._is_bound(node.id)
            and node.id not in self.names
        ):
            self.names.append(node.id)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in node.args.defaults:
            self.visit(default)
        for default in node.args.kw_defaults:
            if default is not None:
                self.visit(default)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in node.args.defaults:
            self.visit(default)
        for default in node.args.kw_defaults:
            if default is not None:
                self.visit(default)
        if node.returns is not None:
            self.visit(node.returns)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.generators, lambda: self.visit(node.elt))

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node.generators, lambda: self.visit(node.elt))

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node.generators, lambda: self.visit(node.elt))

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(
            node.generators,
            lambda: (self.visit(node.key), self.visit(node.value)),
        )

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self._bind_target(node.target)

    def _visit_comprehension(
        self,
        generators: list[ast.comprehension],
        visit_result: callable,
    ) -> None:
        self._bound_scopes.append(set())
        try:
            for generator in generators:
                self.visit(generator.iter)
                self._bind_target(generator.target)
                for condition in generator.ifs:
                    self.visit(condition)
            visit_result()
        finally:
            self._bound_scopes.pop()

    def _bind_target(self, node: ast.AST) -> None:
        if isinstance(node, ast.Name):
            self._bound_scopes[-1].add(node.id)
            return
        if isinstance(node, (ast.Tuple, ast.List)):
            for elt in node.elts:
                self._bind_target(elt)
            return
        if isinstance(node, ast.Starred):
            self._bind_target(node.value)

    def _is_bound(self, name: str) -> bool:
        return any(name in scope for scope in reversed(self._bound_scopes))


class Instrumenter(ast.NodeTransformer):
    def __init__(self, reserved_names: set[str] | None = None) -> None:
        self.i = 0
        self.reserved_names = set(reserved_names or set())
        self.runtime_alias = self._fresh_name("_replay_sem_rt")

    def _fresh_name(self, base: str) -> str:
        if base not in self.reserved_names:
            self.reserved_names.add(base)
            return base
        index = 1
        while f"{base}_{index}" in self.reserved_names:
            index += 1
        name = f"{base}_{index}"
        self.reserved_names.add(name)
        return name

    def rt(self, name: str) -> ast.Attribute:
        return ast.Attribute(ast.Name(self.runtime_alias, ast.Load()), name, ast.Load())

    def tmp(self) -> str:
        self.i += 1
        while f"_replay_sem_tmp_{self.i}" in self.reserved_names:
            self.i += 1
        name = f"_replay_sem_tmp_{self.i}"
        self.reserved_names.add(name)
        return name

    def thunk(self, node: ast.expr) -> ast.Lambda:
        return ast.Lambda(
            args=ast.arguments(
                posonlyargs=[],
                args=[],
                vararg=None,
                kwonlyargs=[],
                kw_defaults=[],
                kwarg=None,
                defaults=[],
            ),
            body=self.visit(node),
        )

    def can_thunk(self, node: ast.AST) -> bool:
        unsafe = (
            ast.Await,
            ast.Yield,
            ast.YieldFrom,
            ast.NamedExpr,
        )
        for child in ast.walk(node):
            if isinstance(child, unsafe):
                return False
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id in {"super", "locals", "globals", "vars"}
            ):
                return False
        return True

    def block(self, statements: list[ast.stmt]) -> list[ast.stmt]:
        out: list[ast.stmt] = []
        for stmt in statements:
            value = self.visit(stmt)
            if value is None:
                continue
            if isinstance(value, list):
                out.extend(value)
            else:
                out.append(value)
        return out

    def visit_Module(self, node: ast.Module) -> ast.Module:
        node.body = self.block(node.body)
        import_stmt = ast.ImportFrom(
            module="replay.semantic_runtime",
            names=[ast.alias(name="RUNTIME", asname=self.runtime_alias)],
            level=0,
        )
        index = 0
        if _is_docstring_stmt(node.body[0]) if node.body else False:
            index = 1
        while index < len(node.body):
            stmt = node.body[index]
            if isinstance(stmt, ast.ImportFrom) and stmt.module == "__future__":
                index += 1
                continue
            break
        node.body.insert(index, import_stmt)
        return node

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
        node.body = self.block(node.body)
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        node.body = self.block(node.body)
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AsyncFunctionDef:
        node.body = self.block(node.body)
        return node

    def visit_Lambda(self, node: ast.Lambda) -> ast.Lambda:
        node.body = self.visit(node.body)
        return node

    def visit_Assign(self, node: ast.Assign):
        if len(node.targets) == 1 and isinstance(node.targets[0], (ast.Tuple, ast.List)):
            tmp = self.tmp()
            return [
                ast.Assign([ast.Name(tmp, ast.Store())], self.visit(node.value)),
                ast.Assign(
                    [node.targets[0]],
                    ast.Call(self.rt("unpack"), [ast.Name(tmp, ast.Load())], []),
                ),
            ]
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Subscript):
            target = node.targets[0]
            return ast.Expr(
                ast.Call(
                    self.rt("setitem"),
                    [self.visit(target.value), self.visit(target.slice), self.visit(node.value)],
                    [],
                )
            )
        node.value = ast.Call(self.rt("assign"), [self.visit(node.value)], [])
        return node

    def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.AnnAssign:
        if node.value is not None:
            node.value = ast.Call(self.rt("assign"), [self.visit(node.value)], [])
        return node

    def visit_AugAssign(self, node: ast.AugAssign):
        ops = {
            ast.Add: "+",
            ast.Sub: "-",
            ast.Mult: "*",
            ast.Div: "/",
            ast.FloorDiv: "//",
            ast.Mod: "%",
            ast.Pow: "**",
            ast.LShift: "<<",
            ast.RShift: ">>",
            ast.BitOr: "|",
            ast.BitAnd: "&",
            ast.BitXor: "^",
            ast.MatMult: "@",
        }
        op = ops.get(type(node.op))
        if isinstance(node.target, ast.Name) and op is not None:
            return ast.Assign(
                [ast.Name(node.target.id, ast.Store())],
                ast.Call(
                    self.rt("assign"),
                    [
                        ast.Call(
                            self.rt("binop"),
                            [ast.Constant(op), ast.Name(node.target.id, ast.Load()), self.visit(node.value)],
                            [],
                        )
                    ],
                    [],
                ),
            )
        node.value = self.visit(node.value)
        return node

    def visit_Delete(self, node: ast.Delete):
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Subscript):
            target = node.targets[0]
            return ast.Expr(
                ast.Call(
                    self.rt("delitem"),
                    [self.visit(target.value), self.visit(target.slice)],
                    [],
                )
            )
        return self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> ast.Return:
        if node.value is not None:
            node.value = self.visit(node.value)
        return node

    def visit_Expr(self, node: ast.Expr) -> ast.Expr:
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node
        node.value = self.visit(node.value)
        return node

    def visit_Call(self, node: ast.Call) -> ast.Call:
        args = [self.visit(arg) for arg in node.args]
        keywords = [ast.keyword(arg=kw.arg, value=self.visit(kw.value)) for kw in node.keywords]
        if isinstance(node.func, ast.Attribute):
            if node.func.attr == "format":
                return ast.Call(self.rt("format_call"), [self.visit(node.func.value), *args], keywords)
            if node.func.attr == "join" and len(args) == 1 and not keywords:
                return ast.Call(self.rt("join_call"), [self.visit(node.func.value), args[0]], [])
            return ast.Call(
                self.rt("call_method"),
                [self.visit(node.func.value), ast.Constant(node.func.attr), *args],
                keywords,
            )
        if isinstance(node.func, ast.Name) and node.func.id in {"super", "locals", "globals"}:
            node.args = args
            node.keywords = keywords
            return node
        return ast.Call(self.rt("call"), [self.visit(node.func), *args], keywords)

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        ops = {
            ast.Add: "+",
            ast.Sub: "-",
            ast.Mult: "*",
            ast.Div: "/",
            ast.FloorDiv: "//",
            ast.Mod: "%",
            ast.Pow: "**",
            ast.LShift: "<<",
            ast.RShift: ">>",
            ast.BitOr: "|",
            ast.BitAnd: "&",
            ast.BitXor: "^",
            ast.MatMult: "@",
        }
        op = ops.get(type(node.op))
        if op is None:
            return self.generic_visit(node)
        return ast.Call(self.rt("binop"), [ast.Constant(op), self.visit(node.left), self.visit(node.right)], [])

    def visit_UnaryOp(self, node: ast.UnaryOp) -> ast.AST:
        ops = {
            ast.Not: "not",
            ast.UAdd: "+",
            ast.USub: "-",
            ast.Invert: "~",
        }
        op = ops.get(type(node.op))
        if op is None:
            return self.generic_visit(node)
        return ast.Call(self.rt("unaryop"), [ast.Constant(op), self.visit(node.operand)], [])

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.AST:
        if not self.can_thunk(node):
            return self.generic_visit(node)
        helper = "bool_and" if isinstance(node.op, ast.And) else "bool_or"
        return ast.Call(self.rt(helper), [self.thunk(value) for value in node.values], [])

    def visit_IfExp(self, node: ast.IfExp) -> ast.AST:
        if not self.can_thunk(node):
            return self.generic_visit(node)
        return ast.Call(
            self.rt("ifexp"),
            [self.thunk(node.test), self.thunk(node.body), self.thunk(node.orelse)],
            [],
        )

    def visit_JoinedStr(self, node: ast.JoinedStr) -> ast.Call:
        parts: list[ast.expr] = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(ast.Tuple([ast.Constant("lit"), value], ast.Load()))
            elif isinstance(value, ast.FormattedValue):
                format_spec = self.visit(value.format_spec) if value.format_spec is not None else ast.Constant(None)
                parts.append(
                    ast.Tuple(
                        [ast.Constant("expr"), self.visit(value.value), ast.Constant(value.conversion), format_spec],
                        ast.Load(),
                    )
                )
        return ast.Call(self.rt("joinedstr"), parts, [])

    def visit_List(self, node: ast.List) -> ast.AST:
        if not isinstance(node.ctx, ast.Load):
            return node
        node.elts = [self.visit(elt) for elt in node.elts]
        return ast.Call(self.rt("pack"), [node], [])

    def visit_Tuple(self, node: ast.Tuple) -> ast.AST:
        if not isinstance(node.ctx, ast.Load):
            return node
        node.elts = [self.visit(elt) for elt in node.elts]
        return ast.Call(self.rt("pack"), [node], [])

    def visit_Set(self, node: ast.Set) -> ast.Call:
        node.elts = [self.visit(elt) for elt in node.elts]
        return ast.Call(self.rt("pack"), [node], [])

    def visit_Dict(self, node: ast.Dict) -> ast.Call:
        node.keys = [self.visit(key) if key is not None else None for key in node.keys]
        node.values = [self.visit(value) for value in node.values]
        return ast.Call(self.rt("pack"), [node], [])

    def visit_comprehension(self, node: ast.comprehension) -> ast.comprehension:
        node.iter = ast.Call(self.rt("iterate"), [self.visit(node.iter)], [])
        node.ifs = [
            ast.Call(self.rt("comp_cond"), [self.visit(condition)], [])
            for condition in node.ifs
        ]
        return node

    def visit_ListComp(self, node: ast.ListComp) -> ast.Call:
        if not self.can_thunk(node):
            node.elt = self.visit(node.elt)
            node.generators = [self.visit(generator) for generator in node.generators]
            return ast.Call(self.rt("pack"), [node], [])
        node.elt = self.visit(node.elt)
        node.generators = [self.visit(generator) for generator in node.generators]
        return ast.Call(self.rt("comprehension"), [ast.Lambda(
            args=ast.arguments(
                posonlyargs=[],
                args=[],
                vararg=None,
                kwonlyargs=[],
                kw_defaults=[],
                kwarg=None,
                defaults=[],
            ),
            body=ast.Call(self.rt("pack"), [node], []),
        )], [])

    def visit_SetComp(self, node: ast.SetComp) -> ast.Call:
        if not self.can_thunk(node):
            node.elt = self.visit(node.elt)
            node.generators = [self.visit(generator) for generator in node.generators]
            return ast.Call(self.rt("pack"), [node], [])
        node.elt = self.visit(node.elt)
        node.generators = [self.visit(generator) for generator in node.generators]
        return ast.Call(self.rt("comprehension"), [ast.Lambda(
            args=ast.arguments(
                posonlyargs=[],
                args=[],
                vararg=None,
                kwonlyargs=[],
                kw_defaults=[],
                kwarg=None,
                defaults=[],
            ),
            body=ast.Call(self.rt("pack"), [node], []),
        )], [])

    def visit_DictComp(self, node: ast.DictComp) -> ast.Call:
        if not self.can_thunk(node):
            node.key = self.visit(node.key)
            node.value = self.visit(node.value)
            node.generators = [self.visit(generator) for generator in node.generators]
            return ast.Call(self.rt("pack"), [node], [])
        node.key = self.visit(node.key)
        node.value = self.visit(node.value)
        node.generators = [self.visit(generator) for generator in node.generators]
        return ast.Call(self.rt("comprehension"), [ast.Lambda(
            args=ast.arguments(
                posonlyargs=[],
                args=[],
                vararg=None,
                kwonlyargs=[],
                kw_defaults=[],
                kwarg=None,
                defaults=[],
            ),
            body=ast.Call(self.rt("pack"), [node], []),
        )], [])

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> ast.GeneratorExp:
        node.elt = self.visit(node.elt)
        node.generators = [self.visit(generator) for generator in node.generators]
        return node

    def visit_Compare(self, node: ast.Compare) -> ast.Call:
        op_names = {
            ast.Eq: "==",
            ast.NotEq: "!=",
            ast.Lt: "<",
            ast.LtE: "<=",
            ast.Gt: ">",
            ast.GtE: ">=",
            ast.In: "in",
            ast.NotIn: "not in",
            ast.Is: "is",
            ast.IsNot: "is not",
        }
        if len(node.ops) == 1 and type(node.ops[0]) in op_names:
            return ast.Call(
                self.rt("compare_op"),
                [ast.Constant(op_names[type(node.ops[0])]), self.visit(node.left), self.visit(node.comparators[0])],
                [],
            )
        if all(type(op) in op_names for op in node.ops) and self.can_thunk(node):
            return ast.Call(
                self.rt("compare_chain"),
                [
                    self.thunk(node.left),
                    ast.Tuple([ast.Constant(op_names[type(op)]) for op in node.ops], ast.Load()),
                    *[self.thunk(comparator) for comparator in node.comparators],
                ],
                [],
            )
        source_args = self.sources(node)
        visited = self.generic_visit(node)
        return ast.Call(self.rt("compare"), [visited, *source_args], [])

    def visit_Subscript(self, node: ast.Subscript) -> ast.AST:
        if not isinstance(node.ctx, ast.Load):
            return self.generic_visit(node)
        return ast.Call(self.rt("subscript"), [self.visit(node.value), self.visit(node.slice)], [])

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        if not isinstance(node.ctx, ast.Load):
            return self.generic_visit(node)
        return ast.Call(self.rt("attr"), [self.visit(node.value), ast.Constant(node.attr)], [])

    def sources(self, node: ast.AST) -> list[ast.expr]:
        collector = NameCollector()
        collector.visit(node)
        return [
            ast.Call(
                self.rt("source"),
                [
                    ast.Lambda(
                        args=ast.arguments(
                            posonlyargs=[],
                            args=[],
                            vararg=None,
                            kwonlyargs=[],
                            kw_defaults=[],
                            kwarg=None,
                            defaults=[],
                        ),
                        body=ast.Name(name, ast.Load()),
                    ),
                    ast.Constant(name),
                ],
                [],
            )
            for name in collector.names
        ]

    def visit_If(self, node: ast.If):
        tmp = self.tmp()
        source_args = self.sources(node.test)
        cond = ast.Assign(
            [ast.Name(tmp, ast.Store())],
            ast.Call(self.rt("cond"), [self.visit(node.test), *source_args], []),
        )
        body = self.block(node.body)
        orelse = self.block(node.orelse)
        wrapped_body = [ast.With([ast.withitem(ast.Call(self.rt("pc"), [ast.Name(tmp, ast.Load())], []), None)], body)]
        wrapped_else = [
            ast.With([ast.withitem(ast.Call(self.rt("pc"), [ast.Name(tmp, ast.Load())], []), None)], orelse)
        ] if orelse else []
        return [cond, ast.If(ast.Name(tmp, ast.Load()), wrapped_body, wrapped_else)]

    def visit_For(self, node: ast.For):
        tmp = self.tmp()
        setup = ast.Assign([ast.Name(tmp, ast.Store())], ast.Call(self.rt("assign"), [self.visit(node.iter)], []))
        body = self.block(node.body)
        loop = ast.For(
            node.target,
            ast.Call(self.rt("iterate"), [ast.Name(tmp, ast.Load())], []),
            [ast.With([ast.withitem(ast.Call(self.rt("pc"), [ast.Name(tmp, ast.Load())], []), None)], body)],
            self.block(node.orelse),
        )
        return [setup, loop]

    def visit_While(self, node: ast.While) -> ast.While:
        tmp = self.tmp()
        source_args = self.sources(node.test)
        body = self.block(node.body)
        orelse = self.block(node.orelse)
        return ast.While(
            ast.Constant(True),
            [
                ast.Assign(
                    [ast.Name(tmp, ast.Store())],
                    ast.Call(self.rt("cond"), [self.visit(node.test), *source_args], []),
                ),
                ast.If(
                    ast.UnaryOp(ast.Not(), ast.Name(tmp, ast.Load())),
                    [*orelse, ast.Break()],
                    [ast.With([ast.withitem(ast.Call(self.rt("pc"), [ast.Name(tmp, ast.Load())], []), None)], body)],
                ),
            ],
            [],
        )

    def visit_With(self, node: ast.With) -> ast.With:
        node.body = self.block(node.body)
        return node

    def visit_AsyncWith(self, node: ast.AsyncWith) -> ast.AsyncWith:
        node.body = self.block(node.body)
        return node

    def visit_Try(self, node: ast.Try) -> ast.Try:
        node.body = self.block(node.body)
        node.orelse = self.block(node.orelse)
        node.finalbody = self.block(node.finalbody)
        for handler in node.handlers:
            handler.body = self.block(handler.body)
        return node


def _is_docstring_stmt(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)


def _reserved_names(tree: ast.AST) -> set[str]:
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


def instrument_tree(tree: ast.AST) -> ast.AST:
    tree = Instrumenter(_reserved_names(tree)).visit(tree)
    ast.fix_missing_locations(tree)
    return tree


def instrument_source(source: str, filename: str = "<unknown>"):
    tree = ast.parse(source, filename=filename)
    tree = instrument_tree(tree)
    return compile(tree, filename, "exec")
